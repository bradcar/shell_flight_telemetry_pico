from utime import ticks_us
from machine import Pin, I2C
from lib.micropython_bmpxxx import bmpxxx

# noinspection PyArgumentList
i2c = I2C(id=0, scl=Pin(13), sda=Pin(12), freq=400_000)

i2c_devices = i2c.scan()
if i2c_devices:
    for d in i2c_devices: print(f"i2c device at address: {hex(d)}")
else:
    print("ERROR: No i2c devices")
print("")

bmp = bmpxxx.BMP585(i2c=i2c, address=0x47)

# OSR & ms     temp
# OSR  press   temp
# 128x 80.4ms  20.8ms
#  64x 40.4ms  10.8ms
#  16x 10.4ms   3.3ms
#   8x  5.4ms
#   4x  2.9ms
#
# Shell Flight: BNO updates at 200 Hz (5ms), to match BMP
# ODR: 200 Hz, OSR: pressure OSR=4x, temp OSR ax  0.41Pa noise @ 100Kp
# IIR set to 0, so no lag in measurements
#
bmp.pressure_oversample_rate = bmp.OSR4
bmp.temperature_oversample_rate = bmp.OSR1
bmp.iir_coefficient = bmp.COEF_0

sea_level_pressure = bmp.sea_level_pressure
print(f"Initial sea_level_pressure = {sea_level_pressure:.2f} hPa")

# reset driver to contain the accurate sea level pressure (SLP) from my nearest airport this hour
# bmp.sea_level_pressure = 1017.0
# bmp.sea_level_pressure = 1021.60
# print(f"Adjusted sea level pressure = {bmp.sea_level_pressure:.2f} hPa\n")

# Alternatively set known altitude in meters and the sea level pressure will be calculated
# https://www.weather.gov/wrh/timeseries?site=KPDX
ALT = 104.851
SARA_B_LINE_WPA_AZ = 299.923
quality_inn = 161  # 2nd floor

bmp.altitude = quality_inn
print(f"Altitude {ALT}m = {bmp.altitude:.2f} meters")
print(f"Adjusted SLP based on known altitude = {bmp.sea_level_pressure:.2f} hPa\n")

print("---- loop ----")
last_ts = ticks_us()
last_pressure = bmp.pressure
while True:
    # Pressure in hPA measured at sensor, temperature in Celsius
    pressure = bmp.pressure
    now = ticks_us()
    if pressure != last_pressure:
        print(f"Sensor pressure = {pressure:.4f} hPa, {(now - last_ts) / 1000.0:.1f} ms")
        last_ts = now
        last_pressure = pressure
        pressure = bmp.pressure
#        print(f"hPa = {pressure:.3f} hPa")

# meters = bmp.altitude
# print(f"Altitude = {meters:.3f} meters")

# temp = bmp.temperature

# print(f"Altitude = {meters:.3f} meters, temp = {temp:.2f} C")

#         feet = meters * 3.28084
#         print(f"Altitude = {int(feet)} feet {int((feet - int(feet))*12)} inches\n")

#     temp = bmp.temperature
#     print(f"temp = {temp:.2f} C")
#
#     # Altitude in meters and in feet/inches

#    time.sleep(1.1)
