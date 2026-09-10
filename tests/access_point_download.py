# access_point_download.py
"""
access_point_download test program to configure AP with a downloadable telemetry log file (~295 KiB)

SSID_STRING = "shell-fi"
PW_STRING = "pyropyro"

download web page: http://192.168.4.1

BSSID: 2C:CF:67:CA:83:3E

The server is single-threaded with server.listen(3). Ff two devices download simultaneously then one will complete first and the second waits.
since downloads are 0.5 second, this should not be an issue.
"""
import gc
import os
import socket
import time

import network
from machine import Pin

from lib import flight_log_config as config

# Status LED
led = Pin("LED", Pin.OUT)

# Access Point (AP) Credentials
SSID_STRING = "shell-fi"
PW_STRING = "pyropyro"
WIFI_CHUNK = 1024  # Standard allocation chunk
DOWNLOAD_COUNTER = 0  # use to number repeated downloads

# telemetry log file name
FILENAME = config.SENSOR_FILE_NAME


def get_file_size(filename):
    """Helper to safely get file size for the HTTP header."""
    try:
        return os.stat(filename)[6]
    except OSError:
        return None


def web_page():
    """Returns a simple download dashboard page."""
    html = f"""<!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Flight Log Download Station</title>
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; background-color: #f4f4f9; }}
            .card {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); display: inline-block; }}
            .btn {{ display: inline-block; background-color: #007bff; color: white; padding: 15px 25px; margin-top: 20px; text-decoration: none; border-radius: 5px; font-weight: bold; }}
            .btn:hover {{ background-color: #0056b3; }}
            .meta {{ color: #666; font-size: 0.9em; margin-top: 10px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Flight Log Downloader</h1>
            <p>Target File: <strong>{FILENAME}</strong></p>
            <a href="/download" class="btn">Download Binary Log</a>
        </div>
    </body>
    </html>"""
    return html


def ap_mode(ssid, password):
    """Activates Access Point Mode."""
    # GC after Setup 
    print(f"\nFree memory before AP setup: {gc.mem_free()} bytes")
    gc.collect()
    print(f"Free memory after  AP setup: {gc.mem_free()} bytes\n")

    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=ssid, password=password)

    while not ap.active():
        time.sleep(0.1)

    ip = ap.ifconfig()[0]
    print("Access Point Mode is active.")
    print(f"Connect to Wi-Fi, then go to: http://{ip}")
    return ip


def run_web_server():
    """Starts the socket server on port 80 with multi-socket request protection."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('', 80))
    server.listen(3)

    print("Web server listening...")

    while True:
        conn, addr = server.accept()
        led.value(1)  # LED on during transfer

        try:
            # Set a short timeout so abandoned backlog connections don't hang the server
            conn.settimeout(0.4)

            # Catch clients/browsers that open speculative sockets and drop them instantly
            request = conn.recv(1024)
            if not request or b' ' not in request:
                conn.close()
                led.value(0)
                continue

            request_line = request.decode('utf-8', 'ignore').split('\r\n')[0]
            parts = request_line.split(' ')
            if len(parts) < 2:
                conn.close()
                led.value(0)
                continue

            path = parts[1]

            # Restore blocking mode for the heavy data transfer phase
            conn.settimeout(None)

        except OSError as e:
            # capture the background speculative connections that Safari slams shut
            if e.args[0] == 104:
                print(f"  Note: Speculative browser handshake dropped cleanly from {addr}")
            elif e.args[0] == 110 or "timeout" in str(e).lower():
                print(f"  Note: Abandoned backlog connection from {addr} timed out cleanly.")
            else:
                print(f"Error: Request initialization error from {addr}: {e}")
            conn.close()
            led.value(0)
            continue

        # Handle Favicon cleanly
        if path == "/favicon.ico":
            try:
                conn.send("HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            finally:
                conn.close()
                led.value(0)
            continue

        # Binary File Download
        if path == "/download":
            global DOWNLOAD_COUNTER
            print(f"\nStarting download to {addr}...")
            print(f"Client: {addr[0]}")
            size = get_file_size(FILENAME)

            if size is None:
                response = "HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n\r\nFile Not Found."
                try:
                    conn.send(response)
                except OSError:
                    pass
                finally:
                    conn.close()
                    led.value(0)
                print(f"Error: {FILENAME} not found on Pico flash.")
                continue

            # Split the filename to add version numbers for multiple downloads
            if "." in FILENAME:
                base, ext = FILENAME.rsplit(".", 1)
                ext = "." + ext
            else:
                base, ext = FILENAME, ""

            # First download has simple full name, additional downloads get version numbers
            if DOWNLOAD_COUNTER == 0:
                download_filename = FILENAME
            else:
                download_filename = f"{base} ({DOWNLOAD_COUNTER}){ext}"

            headers = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/octet-stream\r\n"
                f"Content-Disposition: attachment; filename=\"{download_filename}\"\r\n"
                f"Content-Length: {size}\r\n"
                "Connection: close\r\n\r\n"
            )

            try:
                conn.send(headers)

                total_bytes_sent = 0
                start_time = time.ticks_ms()

                with open(FILENAME, "rb") as f:
                    while True:
                        chunk = f.read(WIFI_CHUNK)
                        if not chunk:
                            break

                        chunk_view = memoryview(chunk)
                        bytes_to_send = len(chunk_view)
                        bytes_sent_acc = 0

                        while bytes_sent_acc < bytes_to_send:
                            sent = conn.send(chunk_view[bytes_sent_acc:])
                            if sent == 0:
                                raise OSError("Error: Wifi Socket connection broken by client")
                            bytes_sent_acc += sent

                        total_bytes_sent += bytes_sent_acc

                end_time = time.ticks_ms()
                duration_ms = time.ticks_diff(end_time, start_time)
                duration_secs = duration_ms / 1000.0

                print("Download complete. Sending socket shutdown flags...")

                mbps = (total_bytes_sent * 8) / 1_000_000 / duration_secs
                print(f"  -> Sent: {total_bytes_sent} bytes as {download_filename}")
                print(f"  -> Time: {duration_secs:.2f} secs")
                print(f"  -> Rate: {mbps:.2f} Mb/s")

                # Increment the version for the next download (wraps at 99)
                DOWNLOAD_COUNTER = (DOWNLOAD_COUNTER % 99) + 1

            except OSError as e:
                print("Error: Active webpage download interrupted mid-stream:", e)
            finally:
                conn.close()
                led.value(0)  # turn LED off at end of download
            continue

        # BASE HANDLER
        # If execution reaches here, it means path is "/" or any other unhandled endpoint.
        # This serves the actual web page markup rather than letting the connection hang.
        print(f"Serving HTML download index page to {addr}")
        response_html = web_page()
        html_bytes = response_html.encode('utf-8')
        html_size = len(html_bytes)

        http_response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {html_size}\r\n"
            "Connection: close\r\n\r\n"
        )

        try:
            conn.send(http_response.encode('utf-8'))
            conn.sendall(html_bytes)
        except OSError as e:
            print("Error: Wifi Connection dropped serving HTML dashboard:", e)
        finally:
            conn.close()
            led.value(0)  # turn LED off at end of download


def main():
    print("Initializing AP Download serving...")
    ap_mode(SSID_STRING, PW_STRING)
    run_web_server()


if __name__ == "__main__":
    main()
