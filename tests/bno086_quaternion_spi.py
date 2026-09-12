# bno086_quaternion_spi.py - Runs on Pico 2 W with BNO086 sensor outputs over USB-C
#
# https://github.com/bradcar/bno08x_i2c_spi_MicroPython
#
# quaternion output at 200 Hz (5 milli-sec) on SPI interface.
# uses efficient sys.stdout.write

import sys
from machine import SPI, Pin
from lib.spi import BNO08X_SPI

int_pin = Pin(14, Pin.IN, Pin.PULL_UP)  # Interrupt, enables BNO to signal when ready
reset_pin = Pin(15, Pin.OUT, value=1)  # Reset to signal BNO to reset

# miso=Pin(16) - BNO SO (POCI)
cs_pin = Pin(17, Pin.OUT, value=1)
# sck=Pin(18)  - BNO SCK
# mosi=Pin(19) - BNO SI (PICO)
wake_pin = Pin(20, Pin.OUT, value=1)  # BNO WAK

uart = machine.UART(0, baudrate=230400)

spi = SPI(0, baudrate=3000000, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
bno = BNO08X_SPI(spi, cs_pin, reset_pin, int_pin, wake_pin)


def main():
    bno.quaternion.enable(200)

    # Sensor provides frequencies at requested 200Hz
    bno.print_report_period()
    print("")

    while True:
        bno.update_sensors()

        if bno.quaternion.updated:
            qr, qi, qj, qk = bno.quaternion

            # print(f"{qr:.4f},{qi:.4f},{qj:.4f},{qk:.4f}")
            output = f"{qr:.4f},{qi:.4f},{qj:.4f},{qk:.4f}\n"
            sys.stdout.write(output)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCtrl-c exit\n")
        bno.print_report_period()

