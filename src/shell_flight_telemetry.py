"""
shell_flight_telemetry.py

Linear_acceleration, Quaternion, Gyro, and pressure (hPa) logging on Raspberry Pi Pico 2 (RP2350)
  - BNO086 is SPI sensor
  - BMP585 is I2C sensor

Log data of 30 seconds of data at 200 Hz = 295 KiB size file

Pico 2 W with Infineon CYW43439 Wi-Fi. A standard MicroPython TCP socket upload is roughly 6 to 9 Mbps
(approx. 750 KB/s to 1.1 MB/s) depending on your distance from the router. Estimated download time 0.3 to 0.4 sec.

Compression output data won't help:
 * Test with sensor data while moving show only a 62% compression on the Mac (down to about 184 KiB from 295 KiB),
   On a Pico, compression would likely take 20 to 40 sec which is far too long.
 * Tests on a still sensor (highly compressable),  would on compress to about 62Kib - highly unlikely in a real case.

CHECKLIST:
 Hour before prepare for flight
 o charge LiPoly battery

 Pre-flight
 o Update Date in flight_log_config.py
 o Update flight number f001, f002, etc. in flight_log_config.py
 o run log_linacc_quat_gyro_hpa_flash_spi.py
   o check calibration when plugged into laptop
   o disconnect laptop, starts logging
   o light fuse within 10 secs
   o make sure logging runs for 30 secs (10 sec disconnect, 6 sec ascent, 10-12 sec drop)

REQUIRES: flight_log_config.py
 * which contains location altitude, log_file_name, and pin configuration !!!

This code checks the IMU calibration accuracy of the bno.linear_acceleration, bno.quaternion, and bno.gyro and
blocks on this until all have a better accuracy >=2 (medium to high). Then saves the calibration to the
BNO086.

After IMU calibration, the bno.linear_acceleration and bno.gyro are measured while not moving to create
a bias correction. The values collected are shown in an ascii histogram. Then the median is used
as the bias correction that is applied to dall data.

Beause of the harsh enviroment which may disable the sensor. This code buffers data in memory and then
writes it to flash. To be efficent for the flash it Buffers in 4 KiB Sector-size, then writes the
sector to flash. This will introdue timing jitter at each sector write.
        write_results_by_sector(bno, rows, filename)

The max size of storage is limited only by the free space on flash. It gathers sensor result in
less than 4 KiB chucks, due to the Flash's 4 KiB Sector size. 4 KiB writes are the most efficient.
At IMU sampling rate of 5ms (200Hz) a sector-size is ~0.43 sec of data. Each flash write and intermittant flush
(duty cycle settable in code) will cause 50 ms to 110 ms jitter in sample collection.

Data Structure (The 48-byte Row):
  packed binary format using struct.pack_into:
  Each Row
  * Timestamp (4 bytes: f)
  * Linear Accel (12 bytes: x, y, z) - Bias corrected
  * Quaternion (16 bytes: r, i, j, k) - For orientation
  * Gyroscope (12 bytes: y, p, r) - Bias corrected
  * Pressure (4 bytes: hpa)

  Footer - end of sector(at CUSTOM_DATA_OFFSET):
  * Sector Index/number 0 start (4-bytes: I)
  * Interrupt Timestamps captured via hardware IRQs (Pins 21 and 22):
     - lift_trigger_ms (4 bytes: f)
     - top_flame_trigger_ms. (4 bytes: f)
  * max_celsius_during_sector (4 bytes: f)
  * max_celsius_ts_ms (4 bytes: f)
  * CRC32: (4 bytes, I) binascii.crc32 is calculated over the first 4092 bytes.

Input:
    *** CAUTION: TIME IN msec NOT SECONDS, for BNO086 efficiency at 5ms 200Hz
    ax, ay, az, acc, ts_ms = bno.linear_acceleration.full
    qr, qi, qj, qk = bno.quaternion
    gy, gp, gr = bno.gyro
    hpa = bmp.pressure

Output:
    *** CAUTION: The created CSV IS CONVERTED TO SECONDS by unpack_bin_sensor_logs.py
    Seconds, lin_acc_x, lin_acc_y, lin_acc_z, quat_r, quat_i, quat_j, quat_k, gyro_y, gyro_p, gyro_r, hpa

    Hawthorne Nevada
    Hawthorne Industrial Airport, NV (ASOS/AWOS - REV)
    Station Elev: 4230.0 ft; Lat/Lon: 38.54482/-118.63137
    https://www.weather.gov/wrh/timeseries?site=KHTH
"""
import binascii  # For fast CRC32
import gc
import os
import struct
from array import array
from sys import implementation

import network
import machine
from machine import SPI, I2C, Pin, ADC
from micropython import const
from lib.micropython_bmpxxx import bmpxxx
from lib.spi import BNO08X_SPI
from utime import sleep_ms, ticks_ms, ticks_diff

from lib import flight_log_config as config

# ==== PIN DEFINITIONS ====
# Internal pins
vsys_adc_pin = ADC(3)
pico_temp_pin = ADC(4)

# External pins
int_pin = Pin(config.PIN_BNO_INT, Pin.IN)  # Interrupt, enables BNO to signal when ready
reset_pin = Pin(config.PIN_BNO_RST, Pin.OUT, value=1)  # Reset to signal BNO to reset

# miso=Pin(16) - BNO SO (POCI)
cs_pin = Pin(config.PIN_BNO_CS, Pin.OUT, value=1)
# sck=Pin(18)  - BNO SCK
# mosi=Pin(19) - BNO SI (PICO)
wake_pin = Pin(config.PIN_BNO_WAKE, Pin.OUT, value=1)  # BNO WAK

# Configure pins with Pull-Up resistors so they stay High until grounded
pin_lift_trig = Pin(config.PIN_LIFT_TRIG, Pin.IN)

# ==== SPI & I2C ====
# noinspection PyArgumentList
spi = SPI(config.SPI_ID, baudrate=3000000, sck=Pin(config.PIN_SCK), mosi=Pin(config.PIN_MOSI),
          miso=Pin(config.PIN_MISO))
bno = BNO08X_SPI(spi, cs_pin, reset_pin, int_pin, wake_pin, debug=False)
# noinspection PyArgumentList
i2c = I2C(id=config.I2C_ID, scl=Pin(config.PIN_I2C_SCL), sda=Pin(config.PIN_I2C_SDA), freq=400_000)
bmp = bmpxxx.BMP585(i2c=i2c, address=config.BMP_ADDR)

# ==== GLOBALS & CONSTANTS ====
# Measured VREF at 3.281V, not 3.3V when powered by USB-C
VREF_MEASURED = 3.281  # data sheet value is 3.3v

# DataLog file:# 4096 = 4032 (84 rows * 48 bytes) + 24 bytes of metadata + 36 null + 4 (CRC)
SECTOR_SIZE = const(4096)  # Exactly 4 KiB
NUM_FLOATS = const(12)
BYTES_PER_ROW = const(48)
ROWS_PER_SECTOR = const(84)
DATA_SIZE = BYTES_PER_ROW * ROWS_PER_SECTOR  # 4032 bytes = 84 * 48
CUSTOM_DATA_OFFSET = DATA_SIZE
CRC_OFFSET = const(4092)  # The very last 4 bytes
pack_string = "<" + (NUM_FLOATS * "f")  # number of f's match count

# GLOBALS for Bias Correction
AX_BIAS = 0.0
AY_BIAS = 0.0
AZ_BIAS = 0.0
GY_BIAS = 0.0
GP_BIAS = 0.0
GR_BIAS = 0.0

# Init lift flag
lift_handled = False
lift_bno_ms = -1000.0
bno_ms = -1000.0


def handle_lift_interrupt(pin):
    """
    update lift_bno_ms with the closest bno timestamp
    WARNING using bno_ms as GLOBAL for very accurate time capture
    """
    global lift_handled, lift_bno_ms, bno_ms
    if lift_handled: return  # debounce
    lift_handled = True
    lift_bno_ms = bno_ms
    pin.irq(handler=None)  # Still detach IRQ, but the flag above is the real "fix"
    # DEBUG ONLY print("LIFT PIN DISCONNECTED - Liftoff!")


def pico_temperature(debug=False):
    """
    Pico on-die temperature
    Pico 2 W rp2350 data sheet on page 1068
    data sheet says 27C  is 0.706v, with a slope of -1.721mV per degree
    RP2350 hardware is natively 12-bit so no need to calc at 16-bit

    :return: Celsius
    """
    raw_temp = pico_temp_pin.read_u16()
    adc_v = ((raw_temp >> 4) / 4095) * VREF_MEASURED
    celsius = 27.0 - (adc_v - 0.706) / 0.001721

    if debug:
        print(f"raw_temp = {raw_temp}")
        print(f"on chip temp = {celsius:.3f}C")
    return celsius


def pico_vsys_voltage(debug=False):
    """
    Pico voltage on Vsys
    reference: https://www.youtube.com/watch?v=Yv5Wf4neLmo
    https://github.com/raspberrypi/pico-examples/blob/master/adc/read_vsys/read_vsys.c
    Note: code changes needed for Pico 2 and Pico, changes were made for W

    with usb-c 5.27v input, measured VBUS (5.259V) and VSYS (4.800V).
    the 0.459V drop is due to internal Schottky diode (D1) on the Pico 2 W
    measured VREF at 3.281V, not 3.3V

    LiPoly Battery & USB-C
    * when Plugged into USB-C: see ~4.8V (USB voltage)
    * Battery Only (Fully Charged): ~3.9V (4.2V LiPoly minus inline 1N5817 diode drop)
    * Battery Only (Near Empty): when >3.3V , the battery is nearly depleted
    """

    # We use a 12-bit scale (>>4 & 4095) because that is the RP2350's native resolution
    raw_vsys = vsys_adc_pin.read_u16() >> 4

    # The Pico 2 W RP2350 Adjustment
    vsys_fudge_factor = 2.332  # adjusted from 3v to match usb-c voltmeter
    vsys = (raw_vsys / 4095) * VREF_MEASURED * vsys_fudge_factor

    #     if debug:
    #         print(f"VSYS Raw (12-bit): {raw_vsys}")
    #         print(f"VREF MEASURED: {VREF_MEASURED}")
    #         print(f"Voltage: {vsys:.2f}V")

    return vsys


def log_info(message, file_handle=None):
    # Always print to REPL
    print(message)

    # If a file handle is provided, write to it
    if file_handle:
        file_handle.write(message + "\n")
        file_handle.flush()  # flush immediately
        try:
            os.sync()  # Force the physical write to flash
        except (OSError, AttributeError):
            pass


def print_get_site_config(log_file=None):
    log_info("\nSite Parameters", log_file)
    log_info("====================================", log_file)
    log_info(f"Data Logging file: {config.SENSOR_FILE_NAME}", log_file)
    log_info(f"Script Log file  : {config.SCRIPT_FILE_NAME}", log_file)
    log_info(f"Date Flight: {config.SITE_DATE}", log_file)
    log_info(f"Address:     {config.SITE_ADDRESS}", log_file)
    log_info(f"GPS Coords:  {config.SITE_GPS}", log_file)
    log_info(f"Alt Meters:  {config.SITE_ELEVATION} m", log_file)
    log_info(f"\n", log_file)


def print_get_sys_config(log_file=None):
    log_info("Start Pico", log_file)
    log_info("====================================", log_file)

    # SPI Config
    spi_str = str(spi)
    spi_req = "baudrate=3000000"  # Required 3M !
    if spi_req in spi_str:
        log_info(spi_str + "\n * SPI Baudrate Correct", log_file)
    else:
        log_info(f" *** SPI ERROR: Expected {spi_req}, got {spi_str}", log_file)

    # I2C Config
    i2c_str = str(i2c)
    i2c_req = "freq=400000"  # Required 400K !
    if i2c_req in i2c_str:
        log_info(i2c_str + "\n * I2C Freq Correct", log_file)
    else:
        log_info(f" *** I2C ERROR: Expected {i2c_req}, got {i2c_str}", log_file)

    i2c1_devices = i2c.scan()
    if i2c1_devices:
        for d in i2c1_devices: log_info(f"i2c1 device{d} = {hex(d)}", log_file)
    else:
        log_info("ERROR: No i2c1 devices", log_file)

    # OS
    log_info(f"\nOS: {implementation[0]} {os.uname()[3]} \n    run on {os.uname()[4]}", log_file)

    # Set Battery voltage Monitor
    # Enable Wireless to flip the internal gate (WL_GPIO2) (C code uses cyw43_arch_init())
    # Enable once at the start of main() and leave it on.
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    log_info(f"set wlan.active(True)", log_file)
    # High-Z Pin 29 for Pico 2 W changes
    machine.Pin(29, machine.Pin.IN, pull=None)
    sleep_ms(20)

    # First Pico Temp
    pico_temp = pico_temperature()
    log_info(f"Pico temp = {pico_temp:.1f}°C", log_file)

    # First Pico Voltage
    pico_battery_voltage = pico_vsys_voltage()
    log_info(f"Pico voltage = {pico_battery_voltage:.3f} volts", log_file)

    # GC after Setup
    log_info(f"\nFree memory before setup: {gc.mem_free()} bytes", log_file)
    gc.collect()
    log_info(f"Free memory after  setup: {gc.mem_free()} bytes\n", log_file)


def print_sensor_status(string, bno, bmp, log_file=None):
    log_info(f"\n{string} Sensor Status", log_file)
    log_info("====================================", log_file)

    # First Pico Temp
    pico_temp = pico_temperature()
    log_info(f"Pico temp = {pico_temp:.1f}°C", log_file)

    # First Pico Voltage
    pico_battery_voltage = pico_vsys_voltage()
    log_info(f"Pico voltage = {pico_battery_voltage:.3f} volts", log_file)

    log_info(f"Lift Trigger Pin State: {pin_lift_trig.value()}", log_file)

    hpa = bmp.pressure
    celsius = bmp.temperature
    meters = bmp.altitude
    feet = meters * 3.28084
    log_info(f"BMP Status: {hpa} hpa, {celsius:.2f}°C", log_file)
    log_info(f"BMP Altitude: {meters:.2f} Meters, {int(feet)}' {int((feet - int(feet)) * 12)}\"\n", log_file)


def write_results_by_sector(bno, bmp, max_rows: int, sensor_file_name: str, log_file=None):
    """
    Write sensor results to file sector by sector. At high-frequency (200 Hz = 4ms updates)
    For 11 floats on BNO086 this is 84 rows or 0.425 sec of data which minimizes data loss when
    sensor in hostile environment.

    The bno sensor times are used as standard for all logs, we do not use Pico's ticks_ms.

    Writes aligned with flash sector sizes picked for most efficient write size.

    Sector size 4096 bytes  = 4032 + 24 + 4 + 36
    * 48 bytes per row (1 timestamp, 3 accel, 4 quat, 3 gyro, 1 hpa)
    *  84 rows * 48 bytes = 4032 bytes
    * Metadata total 28 bytes = 24 + 4
        * 6 fields * 4 bytes = 24 bytes
        * CRC = 4 bytes
        * Padding = 36 bytes

    The disadvantage is about 50-70ms delay at sector write, and > 100ms at flush which will increase
    the jitter at sector boundaries.

    WARNING: Can not measure voltages with this API
    """
    global AX_BIAS, AY_BIAS, AZ_BIAS, GY_BIAS, GP_BIAS, GR_BIAS
    global bno_ms, lift_bno_ms
    global lift_handled, lift_bno_ms, bno_ms

    # Buffer of exactly 4 KiB, data: 4092 CRC: last 4 bytes
    sector_buffer = bytearray(SECTOR_SIZE)

    # localize globals for efficiency
    ax_offset, ay_offset, az_offset = AX_BIAS, AY_BIAS, AZ_BIAS
    gy_offset, gp_offset, gr_offset = GY_BIAS, GP_BIAS, GR_BIAS

    update = bno.update_sensors
    pack_into = struct.pack_into
    crc32 = binascii.crc32
    lin_acc = bno.linear_acceleration
    quat = bno.quaternion
    gyro = bno.gyro

    with open(sensor_file_name, "wb") as f:
        log_info(f"  Opened Sensor Data file: {sensor_file_name}", log_file)

        # Capture Pico's tick_ms for time duration, but NOT for logging timestamps
        start_pico_ms = ticks_ms()
        i = 0
        sector_idx = 0

        # TODO: Understand why sequence of lin_acc, wait on update, and lin_acc is needed to not stall on frist row read
        _, _, _, _, bno_ms = lin_acc.full
        while not update(): pass
        _, _, _, _, bno_ms = lin_acc.full

        # use bno timestamp as ground truth for all data timestamps
        first_bno_ms = bno_ms
        max_celsius = bmp.temperature
        max_celsius_ms = bno_ms

        # capture the log dat and loop until all desired max_rows collected
        while i < max_rows:

            # ** Start New Sector Write: 4096 sector-sized batches
            sector_row_count = 0
            min_accuracy = 4  # notice 3 is highest accuracy

            # zero-fill metadata, padding, and CRC part of sector buffer
            sector_buffer[DATA_SIZE:] = b"\x00" * (SECTOR_SIZE - DATA_SIZE)

            # *** Write a row of data
            update_count = 0
            while sector_row_count < ROWS_PER_SECTOR and i < max_rows:
                if not update():
                    continue

                # bno read (lin_acc, quat, gyro) in 169-184 usec, bmp (hpa, temp) in 837-860 usec. the guess is spi vs i2c
                if lin_acc.updated:
                    ax, ay, az, acc, bno_ms = lin_acc.full
                    qr, qi, qj, qk = quat
                    gy, gp, gr = gyro
                    hpa = bmp.pressure
                    celsius = bmp.temperature

                    if celsius > max_celsius:
                        max_celsius = celsius
                        max_celsius_ms = bno_ms

                    min_accuracy = min(min_accuracy, acc)

                    # 12 values per row (1 timestamp, 3 accel, 4 quat, 3 gyro, 1 hpa)
                    offset = sector_row_count * BYTES_PER_ROW
                    pack_into(pack_string, sector_buffer, offset,
                              bno_ms,
                              ax - ax_offset, ay - ay_offset, az - az_offset,
                              qr, qi, qj, qk,
                              gy - gy_offset, gp - gp_offset, gr - gr_offset,
                              hpa)

                    sector_row_count += 1
                    i += 1

            # ZERO-FILL unused rows and zero metadata(insurance) & padding
            if sector_row_count < ROWS_PER_SECTOR:
                start_fill = sector_row_count * BYTES_PER_ROW
                sector_buffer[start_fill:DATA_SIZE] = b"\x00" * (DATA_SIZE - start_fill)

            vsys_voltage = pico_vsys_voltage()
            # vsys_voltage = 0.0

            # write metadata footer at end of Sector (24 bytes = 6 * 4 bytes)
            struct.pack_into("<IffffI", sector_buffer, CUSTOM_DATA_OFFSET,
                             sector_idx,  # I
                             lift_bno_ms,  # f, lift_bno_ms is updated in Interrupt Handler
                             vsys_voltage,  # f
                             max_celsius,  # f
                             max_celsius_ms,  # f
                             min_accuracy)  # I

            # Calculate CRC over everything EXCEPT the CRC's last 4 bytes (4092 bytes total)
            crc = crc32(memoryview(sector_buffer)[:4092])
            struct.pack_into("<I", sector_buffer, CRC_OFFSET, crc)

            # Write sector to flash:  bytes 0-4091 are data, last 4 bytes are CRC or 0x00 padding
            f.write(sector_buffer)  # Write exactly 4 KiB

            # Flush every other sector (about 1 sec)
            if sector_idx % 2 == 0:
                f.flush()

            # Debug
            # print(f"\nSector {sector_idx}: stats")
            # print(f"sector_row_count: {sector_row_count} of {ROWS_PER_SECTOR}")
            # print(f"Sector Max Celsius: {max_celsius_during_sector:.2f}° C at {max_celsius_ts_ms} ms")
            # print(f"Sector Min Accuracy (Lin_acc): {min_accuracy}")
            # print(f"CRC: {hex(crc)}")
            #
            # if lift_trigger_us > 0 or top_flame_trigger_us > 0:
            #     us_delta = ticks_diff(top_flame_trigger_us, lift_trigger_us)
            #     print(f"\nLift Time (ms): {lift_trigger_ms} ms")
            #     print(f"Top Flame Time: {top_flame_trigger_ms} ms")
            #     print(f"Lift - Top Frame: {us_delta / 1000.0:.5f} ms")

            sector_idx += 1
            # Debug timing for each write, measured 45 ms
            # write_time = ticks_diff(ticks_ms(), write_start)
            # print(f"Sector flushed (4 KiB). Write: {write_time} ms. Total Rows so far: {i}")

        f.flush()
        os.sync()

    pico_ms = ticks_diff(ticks_ms(), start_pico_ms)
    while not bno.update_sensors(): pass
    _, _, _, _, last_bno_ms = bno.linear_acceleration.full

    return i, sector_idx, first_bno_ms, last_bno_ms, pico_ms


def ascii_histogram(data, bins=15, max_width=40, log_file=None):
    """
    Automatically bins data based on min/max and prints an ASCII histogram.
    Works with numpy arrays or standard lists.
    """
    if len(data) == 0:
        print("No data to histogram.")
        return

    d_min, d_max = min(data), max(data)

    # If all data is identical (e.g., all 0.0), create a small range to avoid division by zero
    if d_min == d_max:
        d_max += 0.001

    # Calculate bin edges and counts
    bin_width = (d_max - d_min) / bins
    counts = [0] * bins

    for val in data:
        # Calculate which bin the value belongs to
        idx = int((val - d_min) / bin_width)
        if idx == bins:  # Handle maximum value
            idx -= 1
        counts[idx] += 1

    # Scale bars
    max_count = max(counts)
    scale = max_width / max_count if max_count > 0 else 1.0

    log_info(f"Histogram (Range: {d_min:.6f} to {d_max:.6f})", log_file)
    log_info("-" * (max_width + 35), log_file)

    for i in range(bins):
        b_start = d_min + (i * bin_width)
        b_end = b_start + bin_width

        bar_len = int(counts[i] * scale)
        bar = "#" * bar_len

        # Print row: Range | Count | Bar
        log_info(f"{b_start:10.6f} to {b_end:10.6f} | {counts[i]:5d} | {bar}", log_file)


def get_median(data):
    """Simple median for array.array or list"""
    sorted_data = sorted(data)
    n = len(sorted_data)
    if n % 2 == 1:
        return sorted_data[n // 2]
    else:
        return (sorted_data[n // 2 - 1] + sorted_data[n // 2]) / 2


def get_static_bias_imu(bno, samples=100, log_file=None):
    """
    Calculate residual bias while sensor stable. Do this for number of 'samples'
    """
    global AX_BIAS, AY_BIAS, AZ_BIAS, GY_BIAS, GP_BIAS, GR_BIAS

    log_info(f"Calculating static bias from {samples} samples.", log_file)
    log_info("\n* DO NOT MOVE SENSOR...", log_file)

    ax = array('f', [0.0] * samples)
    ay = array('f', [0.0] * samples)
    az = array('f', [0.0] * samples)
    gy = array('f', [0.0] * samples)
    gp = array('f', [0.0] * samples)
    gr = array('f', [0.0] * samples)

    for i in range(20):
        bno.update_sensors()
        if bno.linear_acceleration.updated:
            new_ax, new_ay, new_az = bno.linear_acceleration
            new_gy, new_gp, new_gr = bno.gyro

    idx = 0
    while idx < samples:
        bno.update_sensors()
        if bno.linear_acceleration.updated:
            new_ax, new_ay, new_az = bno.linear_acceleration
            new_gy, new_gp, new_gr = bno.gyro

            # Store in float arrays
            ax[idx], ay[idx], az[idx] = new_ax, new_ay, new_az
            gy[idx], gp[idx], gr[idx] = new_gy, new_gp, new_gr

            idx += 1

    # There can be significant outliers, using median instead of average
    AX_BIAS = get_median(ax)
    AY_BIAS = get_median(ay)
    AZ_BIAS = get_median(az)
    GY_BIAS = get_median(gy)
    GP_BIAS = get_median(gp)
    GR_BIAS = get_median(gr)

    log_info("\nascii Histograms of Acceleration Biases (m/s²):", log_file)
    log_info(f"\nAx bias = median(ax) = {AX_BIAS:.7f}", log_file)
    ascii_histogram(ax, bins=15, log_file=log_file)
    log_info(f"\nAy bias = median(ay) = {AY_BIAS:.7f}", log_file)
    ascii_histogram(ay, bins=15, log_file=log_file)
    log_info(f"\nAz bias = median(az) = {AZ_BIAS:.7f}", log_file)
    ascii_histogram(az, bins=15, log_file=log_file)

    log_info("\nascii Histograms of Gyroscope Biases (rad/s):", log_file)
    log_info(f"\nGy bias = median(gy) = {GY_BIAS:.7f}", log_file)
    ascii_histogram(gy, bins=15, log_file=log_file)
    log_info(f"\nGp bias = median(gp) = {GP_BIAS:.7f}", log_file)
    ascii_histogram(gp, bins=15, log_file=log_file)
    log_info(f"\nGr bias = median(gr) = {GR_BIAS:.7f}", log_file)
    ascii_histogram(gr, bins=15, log_file=log_file)


def sensor_calibration_imu(bno, stable_sec):
    """
    Sensor calibration, must be stable for stable_sec.
    TODO no max for timeout
    """
    print(f"\nCalibration: Continue for {stable_sec} secs of Medium(2) to High(3) Accuracy\n")
    start_good = None
    calibration_good = False
    status = ""

    # Begin calibration, Wait sensor to be ready to calibrate
    bno.begin_calibration()
    bno.calibration_status()

    last_print = ticks_ms()
    while True:
        bno.update_sensors()

        # only print every .2 sec (200 ms)
        if ticks_diff(ticks_ms(), last_print) < 200:
            continue
        last_print = ticks_ms()

        _, _, _, lin_accl_accuracy, _ = bno.linear_acceleration.full
        _, _, _, gyro_accuracy, _ = bno.gyro.full
        _, _, _, _, quat_accuracy, _ = bno.quaternion.full

        if all(x >= 2 for x in (lin_accl_accuracy, gyro_accuracy, quat_accuracy)):
            status = f"All Sensors >= {min(lin_accl_accuracy, gyro_accuracy, quat_accuracy)} (>2 needed)"
            calibration_good = True
        else:
            if start_good is not None:
                print("\nlost calibration, resetting timer\n")
            status = "low accuracy, suggest moving sensor"
            calibration_good = False

        print(f"Accuracy: accel={lin_accl_accuracy}, gyro={gyro_accuracy}, quat={quat_accuracy}   {status}")

        if calibration_good:
            if start_good is None:
                start_good = ticks_ms()
                print(f"Calibration >=2 on all sensors. Start {stable_sec}-second timer...\n")
            else:
                elapsed = ticks_diff(ticks_ms(), start_good) / 1000.0
                if elapsed >= stable_sec:
                    print(f"*** Calibration stable for {stable_sec} secs")
                    break
        else:
            start_good = None

    bno.save_calibration_data()
    print("*** Calibration saved")
    return lin_accl_accuracy, gyro_accuracy, quat_accuracy


def main():
    # Open file with append mode, failsafe against deleting previous log without saving
    try:
        log_file = open(config.SCRIPT_FILE_NAME, "a")
    except Exception as e:
        print(f"ERROR Failed to open log file: {e}, will only print to REPL")
        log_file = None

    print_get_site_config(log_file)
    print_get_sys_config(log_file)

    # ==== Barometer Set up ====
    log_info("Barometer Setup", log_file)
    log_info("====================================", log_file)
    bmp.pressure_oversample_rate = bmp.OSR4
    bmp.temperature_oversample_rate = bmp.OSR1
    bmp.iir_coefficient = bmp.COEF_0

    log_info(f"Original SLP based @ std 1013.25 alt = {bmp.sea_level_pressure:.2f} hPa\n", log_file)
    log_info(f"Altitude = {bmp.altitude:.2f} meters", log_file)

    bmp.altitude = config.SITE_ELEVATION
    log_info(f"Adjusted SLP based on known altitude = {bmp.sea_level_pressure:.2f} hPa\n", log_file)

    # ==== IMU Set up ====
    # Update frequency in Hz, 200Hz = 5ms sample
    # very slow for orientation testing: 10Hz = 100ms
    log_info("IMU Setup", log_file)
    log_info("====================================", log_file)
    accel_hertz = bno.linear_acceleration.enable(config.SAMPLE_FREQ_HZ)
    gyro_hertz = bno.gyro.enable(config.SAMPLE_FREQ_HZ)
    quat_hertz = bno.quaternion.enable(config.SAMPLE_FREQ_HZ)

    bno.print_report_period()
    log_info("BNO08x sensors enabled\n", log_file)
    log_info(f"Linacc = {accel_hertz} Hz", log_file)
    log_info(f"gyro   = {gyro_hertz} Hz", log_file)
    log_info(f"quat   = {quat_hertz} Hz ", log_file)

    # ==== Calibration Set up ====
    # CAUTION: check calibration will NOT time out if inaccurate
    stable_sec = config.CALIB_REQUIRED_STABLE_SEC
    lin_accl_accuracy, gyro_accuracy, quat_accuracy = sensor_calibration_imu(bno, stable_sec=stable_sec)
    log_info(f"Final Accuracy: lin_accel={lin_accl_accuracy}, gyro={gyro_accuracy}, quat={quat_accuracy}\t (3 is best)",
             log_file)

    # ==== Static Bias Set up  ====
    # Sensor Bias calculation: DO Not move sensor, 1 second or 200 samples at 200 Hz
    log_info(f"\nSleeping for 5 sec, to provide time to stabilize device", log_file)
    sleep_ms(5000)
    log_info(f"\nStarting Bias calibration for {config.BIAS_REQUIRED_SEC} Sec...", log_file)
    bias_sample_count = config.SAMPLE_FREQ_HZ * config.BIAS_REQUIRED_SEC
    get_static_bias_imu(bno, samples=bias_sample_count, log_file=log_file)
    log_info(f"\nStatic Acceleration Biases: {AX_BIAS=:+.6f}, {AY_BIAS=:+.6f}, {AZ_BIAS=:+.6f}", log_file)
    log_info(f"Static Gyro Biases:         {GY_BIAS=:+.6f}, {GP_BIAS=:+.6f}, {GR_BIAS=:+.6f}", log_file)

    # GC after calibration & Bias calculation
    log_info(f"\nFree memory before gc.collect: {gc.mem_free()} bytes", log_file)
    gc.collect()
    log_info(f"Free memory after  gc.collect: {gc.mem_free()} bytes", log_file)

    # ==== IRQ Setup ====
    # Attach IRQs for FALLING edge (High to Low)
    pin_lift_trig.irq(trigger=Pin.IRQ_FALLING, handler=handle_lift_interrupt)

    # === Print Sensor Status ====
    print_sensor_status("Initial", bno, bmp, log_file)

    # ==== BLOCK DATA LOGGING until USB cable Disconnect laptop ====
    log_info(f"Ready for flight: Wait for voltage to drop after USB disconnect...", log_file)

    volts = pico_vsys_voltage()
    # USB-C Power:    ~5.2v typical volt (~4.8v at VSYS)
    # 4.5v to detect USB-C to LiPoly Battery transition
    # LiPoly Battery: ~4.2v full charge  (~4.0v at VSYS)
    # LiPoly Battery: <3.3V when empty   (~4.1v at VSYS)

    #     while volts > config.USB_DISCONNECT_THRESHOLD:
    #         volts = pico_vsys_voltage()
    #         print(f"Waiting... Current VSYS: {volts:.3f}v, Lift Pin: {'' if pin_lift_trig.value() == 1 else '*Not* '}connected      ", end="\r")
    #         sleep_ms(100)

    log_info(f"\nUSB Disconnect @{volts:.3f}v: Starting flight logging ...", log_file)

    # ==== Start Data Collection ====
    log_info(f"\nStart logging...", log_file)

    # 5 ms sample period generates 200 rows/sec, 30 secs generates 288KiB logging data
    # ~0.42 sec per 4 KiB sector ~85 rows
    # ISSUE: ~100ms jitter at sector writes
    log_info(f"\nSTART Flight Logging: {config.FLIGHT_DURATION_SEC}s at {config.SAMPLE_FREQ_HZ}Hz", log_file)
    max_rows = config.FLIGHT_DURATION_SEC * config.SAMPLE_FREQ_HZ
    try:
        rows_logged, sectors, first_sensor_ms, last_sensor_ms, pico_ms = write_results_by_sector(bno, bmp, max_rows,
                                                                                                 config.SENSOR_FILE_NAME,
                                                                                                 log_file)
    finally:
        os.sync()

    # ==== Final Data Collection Summary ====
    log_info(f"\nEnd Data Collection: to file= {config.SENSOR_FILE_NAME}", log_file)

    log_info(f"  Setup-to-flight duration: {(last_sensor_ms - first_sensor_ms) / 1000:.1f} s", log_file)
    log_info(f"  Number of sectors: {sectors} ", log_file)
    log_info(f"  Logged {rows_logged} Rows, {BYTES_PER_ROW=}", log_file)
    kbytes = (BYTES_PER_ROW * max_rows) / 1024
    log_info(
        f"  Log size = {(max_rows * BYTES_PER_ROW)} ({kbytes:.1f} KiB), xfer = {kbytes / (pico_ms / 1000):.1f} KiB/s",
        log_file)
    log_info(f"  Timestamp {first_sensor_ms=} to {last_sensor_ms=}", log_file)
    # log_info(f"  Sensor ave = {(last_sensor_ms - first_sensor_ms) / max_rows:.2f} ms/report", log_file)
    log_info(f"  RaspPi ave = {(pico_ms / max_rows):.2f} ms/reports\n", log_file)

    print_sensor_status("Final", bno, bmp, log_file)

    os.sync()

    # Blink LED uses Wi-Fi hardware which interfers with voltage ADC so don't try to use it until end signal needed
    def blink(timer):
        led.toggle()

    from machine import Timer

    led = Pin("LED", Pin.OUT)
    timer = Timer(-1)
    timer.init(freq=20, mode=Timer.PERIODIC, callback=blink)
    sleep_ms(5000)
    timer.deinit()  # stop hardware timer firing
    sleep_ms(50)
    led.off()


if __name__ == "__main__":
    main()

