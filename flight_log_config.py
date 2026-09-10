# flight_log_config.py
from micropython import const

# --- FLIGHT/LOCATION PARAMETERS ---
# File for flight Log Data
DATE_STR = "20260520"
FLIGHT_STR = "f06"
SENSOR_FILE_NAME = f"flight_log_{DATE_STR}_{FLIGHT_STR}.bin"
SCRIPT_FILE_NAME = f"script_log_{DATE_STR}_{FLIGHT_STR}.txt"

# Location Parameters
SITE_DATE = f"{DATE_STR[4:6]}/{DATE_STR[6:8]}/{DATE_STR[0:4]}"
SITE_ADDRESS = "343 Howanut Rd, Oakville, WA 98658"
SITE_GPS = "46.81593° N, 123.18358° W"
SITE_ELEVATION = 29.87  # Meters above sea level, Chehalis 19.87m = 98'

# Flight Parameters
FLIGHT_DURATION_SEC = 3  # 30 sec?
SAMPLE_FREQ_HZ = 200

# Calibration Settings
CALIB_REQUIRED_STABLE_SEC = 3  # Calibration stable required seconds
BIAS_REQUIRED_SEC = 1  # Collect 1 second of data for bias

# USB voltage 
USB_DISCONNECT_THRESHOLD = 4.5  # Detect voltage drop Trigger when USB-C disconnect and VSYS drops below 4.2V

# ===============================================================
# --- Hardware Pin Assignments ---
# SPI 0 for BNO086
SPI_ID = const(0)
PIN_BNO_INT = const(14)
PIN_BNO_RST = const(15)
PIN_MISO = const(16)
PIN_BNO_CS = const(17)
PIN_SCK = const(18)
PIN_MOSI = const(19)
PIN_BNO_WAKE = const(20)

# I2C 0 for BMP585
I2C_ID = const(0)
PIN_I2C_SDA = const(12)
PIN_I2C_SCL = const(13)
BMP_ADDR = 0x47

# Interrupt Pins (Payload Triggers)
PIN_LIFT_TRIG = const(21)
#PIN_FLAME_TRIG = const(22)

# 3.3v ─ link ─+─ GPIO
#              |
#            10 kΩ
#              |
#             GND
                                       
