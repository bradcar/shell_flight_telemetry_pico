#
"""
access_point_download.py

Access Point (AP) download test program to configure AP with a downloadable telemetry log file (~295 KiB)

download web page: http://192.168.4.1

BSSID: 2C:CF:67:CA:83:3E

This is a single-threaded server with server.listen(3).
If two devices download simultaneously then one will complete first and the second waits.
since downloads are 0.5 second, this should not be an issue.
"""
import gc
import os
import socket
import time

import network
from machine import Pin

import flight_log_config as config

# Status LED
led = Pin("LED", Pin.OUT)

# Access Point (AP) Credentials
SSID_STRING = "shell-fi"
PW_STRING = "pyropyro"


def ap_mode(ssid, password):
    """Standard Wi-Fi Access Point setup routine."""
    print(f"\nFree memory before AP setup: {gc.mem_free()} bytes")
    gc.collect()
    print(f"Free memory after GC: {gc.mem_free()} bytes\n")

    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=ssid, password=password)

    # Retry loop with timeout guard
    retries = 50
    while not ap.active() and retries > 0:
        time.sleep(0.1)
        retries -= 1

    if not ap.active():
        raise RuntimeError("Failed to activate Wi-Fi Access Point interface.")

    ip = ap.ifconfig()[0]
    print("Access Point Mode is active.")
    print(f"Connect to Wi-Fi network: {ssid} \nDownload page at: http://{ip}\n")
    return ip


def get_file_size(filename):
    """Check file existence and size."""
    try:
        return os.stat(filename)[6]
    except OSError:
        return None


def send_file_stream(conn, filename, stream_buffer):
    """Streams binary file directly over socket using slice view on stream_buffer."""
    total_bytes_sent = 0
    start_time = time.ticks_ms()
    buf_view = memoryview(stream_buffer)

    gc.collect()  # Clean transient garbage prior to file streaming
    with open(filename, "rb") as f:
        while True:
            bytes_read = f.readinto(stream_buffer)
            if not bytes_read:
                break

            bytes_sent_acc = 0
            while bytes_sent_acc < bytes_read:
                # send() returns sent byte count, sendall() returns None
                sent = conn.send(buf_view[bytes_sent_acc:bytes_read])
                if sent == 0:
                    raise OSError("Error: Wifi Socket connection broken by client")
                bytes_sent_acc += sent

            total_bytes_sent += bytes_sent_acc

    duration_ms = time.ticks_diff(time.ticks_ms(), start_time)
    duration_secs = duration_ms / 1000.0
    print("Download complete. Sending socket shutdown flags.")
    return duration_secs, total_bytes_sent


def parse_request(conn, addr):
    """HTTP request-line parser with non-blocking timeout handling and socket cleanup."""
    try:
        conn.settimeout(0.4)
        request = conn.recv(1024)
        if not request or b' ' not in request:
            return None

        request_line = request.decode('utf-8', 'ignore').split('\r\n')[0]
        parts = request_line.split(' ')
        if len(parts) < 2:
            return None

        return parts[1]
    except OSError as e:
        if e.args[0] == 104:
            print(f"  Note: Speculative browser handshake dropped cleanly from {addr}")
        elif e.args[0] == 110 or "timeout" in str(e).lower():
            print(f"  Note: Abandoned backlog connection from {addr} timed out cleanly.")
        else:
            print(f"Error: Request initialization error from {addr}: {e}")
        return None
    finally:
        conn.settimeout(None)


def send_html_page(conn, html_content):
    html_bytes = html_content.encode('utf-8')
    headers = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(html_bytes)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode('utf-8')

    try:
        conn.sendall(headers + html_bytes)
    except OSError as e:
        print("Error: Wifi Connection dropped serving HTML dashboard:", e)


def get_download_filename(filename, counter):
    """Formats file download name with sequential version counter."""
    if counter == 0:
        return filename

    if "." in filename:
        base, ext = filename.rsplit(".", 1)
        return f"{base} ({counter}).{ext}"
    return f"{filename} ({counter})"


def handle_file_download(conn, filename, counter, stream_buffer):
    """Handles header generation, file streaming, and metrics printout."""
    size = get_file_size(filename)
    if size is None:
        try:
            conn.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n\r\nFile Not Found.")
        except OSError:
            pass
        print(f"Error: {filename} not found on flash.")
        return counter

    download_name = get_download_filename(filename, counter)
    headers = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/octet-stream\r\n"
        f'Content-Disposition: attachment; filename="{download_name}"\r\n'
        f"Content-Length: {size}\r\n"
        "Connection: close\r\n\r\n"
    )

    try:
        conn.sendall(headers.encode('utf-8'))
        duration_secs, total_bytes_sent = send_file_stream(conn, filename, stream_buffer)

        if duration_secs > 0:
            mbps = (total_bytes_sent * 8) / 1_000_000 / duration_secs
            print(f"  -> Sent: {total_bytes_sent} bytes as {download_name}")
            print(f"  -> Time: {duration_secs:.2f} secs")
            print(f"  -> Rate: {mbps:.2f} Mb/s")

        return (counter % 99) + 1

    except OSError as e:
        print("Error: Active webpage download interrupted mid-stream:", e)
        return counter


# ---- TODO USE ABOVE GENERIC METHODS TO CREATE wifi_web_utils.py -------------------------------------------------


def download_web_page(filename):
    """Returns download dashboard page HTML."""
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
    download_counter = 0
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
                print(f"\nStarting download (#{download_counter}) to {addr} (Client: {addr[0]})...")
                download_counter = handle_file_download(conn, filename, download_counter, stream_buffer)
                continue

            # Base route ("/" or index)
            print(f"Serving HTML download index page to {addr}")
            send_html_page(conn, download_web_page(filename))

        finally:
            conn.close()
            led.value(0)
            gc.collect()


def main():
    filename = config.SENSOR_FILE_NAME
    wifi_chunk = 2048  # efficient TCP payload streaming on Pico W
    stream_buffer = bytearray(wifi_chunk)  # Zero-allocation static heap buffer

    print("Initializing Pico's AP (Access Point) Download serving...")
    ap_mode(SSID_STRING, PW_STRING)
    run_web_server(filename, stream_buffer)


if __name__ == "__main__":
    main()
