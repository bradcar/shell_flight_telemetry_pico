# shell_flight_telemetery_pico
shell telemetry data collection on Pi Pico. 
Data from the IMU and pressure sensors are logged sector by sector into a file.
Code also calibrate IMU to correct for biases before flight telemetry is started.

## Basic code outline

* Initializes the Pico 2 W, BNO086 SPI interface, and BMP585 I2C interface. 
* Enables the BNO086 linear acceleration, gyro, and quaternion reports at config.SAMPLE_FREQ_HZ.
* Runs BNO calibration until acceleration, gyro, and quaternion all reach accuracy ≥ 2.
* Collects stationary IMU data and uses the median of each axis as a static bias.

Data-logging loop:
1. Collects FLIGHT_DURATION_SEC × SAMPLE_FREQ_HZ rows.
2. Packs each row into a 48-byte binary record.
3. Accumulates 84 records = 4,032 bytes.
4. Adds 28 bytes of metadata and 36 bytes of padding.
5. Calculates a CRC32 over the first 4,092 bytes. The writes the complete 4,096-byte sector.
6. Repeats until the requested number of samples has been collected.

## File Sector logs:

Writes aligned with flash sector sizes picked for most efficient write size.

    Sector size 4096 bytes  = 4032 + 24 + 4 + 36
    * 48 bytes per row (1 timestamp, 3 accel, 4 quat, 3 gyro, 1 hpa)
    *  84 rows * 48 bytes = 4032 bytes
    * Metadata total 28 bytes = 24 + 4
        * 6 fields * 4 bytes = 24 bytes
        * CRC = 4 bytes
        * Padding = 36 bytes