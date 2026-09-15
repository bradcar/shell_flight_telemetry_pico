#
"""
access_point_download.py

Access Point (AP) download test program for MicroPython (Raspberry Pi Pico W).
Configures an AP interface to serve a downloadable telemetry log file (~288 KiB)
over a fast lightweight HTTP server.

Network Configuration:
    - SSID          : shell-fi
    - Channel       : 11
    - Password      : pyropyro
    - Hardware BSSID: 2C:CF:67:CA:83:3E

Server & Concurrency Architecture:
    - Single-threaded blocking TCP server listening on port 80 with socket backlog of 3.
    - Serves one connection at a time. Concurrent client requests are queued in
      the socket backlog. Typical log downloads take ~0.5 seconds, which avoids timeouts.
    - Utilizes a zero-allocation static bytearray buffer ("memoryview") for chunked
      file transfers.

Major Functions:
    - ap_mode(ssid, password, channel):
      Activates the Wi-Fi Access Point interface, checks link readiness with
      a timeout loop, and returns the IP address.

    - run_web_server(filename, stream_buffer):
      Main web server loop. Binds socket port 80. Accepts incoming TCP client connections,
      toggles the status LED during active requests.
        * When a user types the ip returned by ap_mode in their browser, it requests the root path (/).
          root path (/) or /index.html request start send_html_page(), which renders the page with "Download Binary Log" button.
        * When the user clicks the download button, the browser requests (ex: http://192.168.4.1/download)
          The code handles /download and routes it to handle_file_download(), which streams the .bin flight log file to their device.
        * Browsers automatically ask for a website icon (favicon) every time they visit a page.
          The code catches /favicon.ico and immediately responds with 204 No Content to tell the browser
          "there is no icon" quickly and silently.

    - disable_ap():
      Deactivates the WLAN AP interface during system shutdown or error exit.
"""
import gc
import socket

from machine import Pin

import shell_flight_telemetry_config as config
from lib.wifi_web_utils import ap_mode, parse_request, send_html_page, handle_file_download, disable_ap

# Status LED
led = Pin("LED", Pin.OUT)


def download_web_page(filename):
    """
    Returns download dashboard page HTML.

    Page with simple blue download button.
    """
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Flight Log Download Station</title>
    <style>
        body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; background-color: #f4f4f9; }}
        .card {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); display: inline-block; }}
        .btn {{ display: inline-block; background-color: #007bff; color: white; padding: 15px 25px; margin-top: 20px; text-decoration: none; border-radius: 5px; font-weight: bold; }}
        .btn:hover {{ background-color: #0056b3; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Flight Log Downloader</h1>
        <p>Target File: <strong>{filename}</strong></p>
        <a href="/download" class="btn">Download Binary Log</a>
    </div>
</body>
</html>"""


def run_web_server(filename, stream_buffer):
    """
    Main web server loop. Binds socket port 80. Accepts incoming TCP client connections,
    toggles the status LED during active requests.

    * When a user types http://192.168.4.1 in their browser, it requests the root path (/).
      The code routes this request to send_html_page(), which renders the web page with the "Download Binary Log" button.
    * When the user clicks the download button, the browser requests http://192.168.4.1/download.
      The code identifies /download and routes it to handle_file_download(), which streams the .bin flight log file to their device.
    * Browsers automatically ask for a website icon (favicon) every time they visit a page.
      The code catches /favicon.ico and immediately responds with 204 No Content to tell the browser
      "there is no icon" quickly and silently.

    :param filename:
    :param stream_buffer:
    """
    download_counter = 0
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server.bind(('', 80))
        server.listen(3)
        print("Web server listening...")

        while True:
            conn, addr = server.accept()
            led.value(1)

            try:
                path = parse_request(conn, addr)
                if path is None:
                    continue

                if path == "/favicon.ico":
                    try:
                        conn.sendall(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
                    except OSError:
                        pass
                    continue
                if path == "/download":
                    print(f"\nStarting download (#{download_counter}) from Client: {addr[0]}, {addr[1]} .")
                    download_name, download_counter, total_bytes_sent, duration_secs = handle_file_download(conn, filename,
                                                                                             download_counter,
                                                                                             stream_buffer)

                    if duration_secs > 0:
                        print("Download data transfer complete.")
                        mbps = (total_bytes_sent * 8) / 1_000_000 / duration_secs
                        print(f"  -> Sent: {total_bytes_sent} bytes as {download_name}")
                        print(f"  -> Time: {duration_secs:.2f} secs")
                        print(f"  -> Rate: {mbps:.2f} Mb/s")
                    continue
                if path == "/" or path == "/index.html":
                    send_html_page(conn, download_web_page(filename))
                else:
                    send_404_page(conn)

            finally:
                conn.close()
                led.value(0)
                gc.collect()
    except KeyboardInterrupt:
        print("\nCtrl-C  Stopping web server...")
    except OSError as e:
        print(f"\nServer socket error: {e}")
    finally:
        server.close()
        print("Server socket closed.")


def main():
    wifi_chunk = 2048  # Efficient TCP payload streaming on Pico W
    stream_buffer = bytearray(wifi_chunk)  # Zero-allocation static heap buffer

    print("Initializing Pico's AP (Access Point) Download serving...")
    try:
        ssid = config.SSID_STRING
        channel = config.SSID_CHANNEL
        ip = ap_mode(ssid, config.PW_STRING, config.SSID_CHANNEL)

        if ip is not None:
            print("\nAccess Point Mode is active, can log into network.\n")
            print(f"Connect to Wi-Fi network: {ssid}")
            print(f"Download page at: http://{ip}/\n")
        else:
            raise RuntimeError(f"AP is not active and has no valid IPv4 address for : {ssid}")

        # Start the HTTP server for the download page and file.
        run_web_server(config.SENSOR_FILE_NAME, stream_buffer)

    except KeyboardInterrupt:
        print("\nCtrl-C Program terminated by user.")
    finally:
        disable_ap()
        led.value(0)
        print("Cleanup complete.")


if __name__ == "__main__":
    main()
