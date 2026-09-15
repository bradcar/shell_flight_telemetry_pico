"""
wifi_web_utils.py

ap_mode - Initialize and activate the Wi-Fi Access Point with credentials and returns the assigned IP address.
get_file_size - Checks file existence and returns its total size in bytes, or None if missing.
send_file_stream - Streams binary file directly over a socket using a static buffer to eliminate heap allocations.
parse_request - Parses the HTTP request path from an incoming socket connection with non-blocking timeout.
send_html_page - Formats and transmits standard HTTP headers along with HTML content over the socket.
get_download_filename - Formats the output download filename by appending a sequential version counter prior to the file extension.
handle_file_download - downloads file, including 404 responses, header delivery, streaming, and throughput logging.
disable_ap - Safely deactivates and powers down the active Wi-Fi Access Point interface.
"""
import gc
import network
import time

import os

REQUEST_TIMEOUT = 0.4


def ap_mode(ssid, password, channel=11):
    """Standard Wi-Fi Access Point setup routine."""
    gc.collect()
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(ssid=ssid, password=password, channel=channel)

    # Retry loop waiting for interface activation AND valid IP assignment
    deadline = time.ticks_add(time.ticks_ms(), 5000)  # 5 second timeout
    ip = "0.0.0.0"

    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if ap.active():
            ip = ap.ifconfig()[0]
            if ip != "0.0.0.0":
                break
        time.sleep_ms(50)

    if not ap.active() or ip == "0.0.0.0":
        raise RuntimeError("Failed to activate Wi-Fi Access Point interface or assign IP.")
    
    print(f"ap_mode: SSID: {ssid!r}, channel: {channel}")

    return ip


def get_file_size(filename):
    """Check file existence and size."""
    try:
        return os.stat(filename)[6]
    except OSError as e:
        print(f"Error: Unable to access {filename!r} on flash: {e}.")
        return None


def send_file_stream(conn, filename, stream_buffer):
    """
    Streams binary file directly over socket using slice view on stream_buffer.

    The caller owns the socket and is responsible for closing it.
    """
    total_bytes_sent = 0
    start_time = time.ticks_ms()
    buf_view = memoryview(stream_buffer)

    gc.collect()  # Garbage collect prior to file streaming
    with open(filename, "rb") as f:
        while True:
            bytes_read = f.readinto(stream_buffer)
            if not bytes_read:
                break

            bytes_sent_acc = 0
            while bytes_sent_acc < bytes_read:
                # send() returns sent byte count, sendall() returns None
                sent = conn.send(buf_view[bytes_sent_acc:bytes_read])
                if sent is None or sent <= 0:
                    raise OSError("Error: Wifi Socket connection broken by client")
                bytes_sent_acc += sent

            total_bytes_sent += bytes_sent_acc

    duration_ms = time.ticks_diff(time.ticks_ms(), start_time)
    duration_secs = duration_ms / 1000.0
    return total_bytes_sent, duration_secs


def parse_request(conn, addr):
    """HTTP request-line parser with non-blocking timeout handling and socket cleanup."""
    try:
        conn.settimeout(REQUEST_TIMEOUT)
        request = conn.recv(1024)
        if not request or b' ' not in request:
            return None

        request_line = request.decode("utf-8", "ignore").split("\r\n", 1)[0]
        parts = request_line.split()

        if len(parts) < 2:
            return None

        method = parts[0]
        path = parts[1]

        if method != "GET":
            print(f"  Ignoring unsupported HTTP method {method!r} from {addr}")
            return None

        return path

    except OSError as e:
        error_code = e.args[0] if e.args else None
        error_text = str(e).lower()

        if "timeout" in error_text or error_code == 110:
            print(f"  Note: Request from {addr} timed out.")
        elif "reset" in error_text or error_code == 104:
            print(f"  Note: Client {addr} reset the connection.")
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
        return None, counter, 0, 0.0

    download_name = get_download_filename(filename, counter)
    headers = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/octet-stream\r\n"
        f'Content-Disposition: attachment; filename="{download_name}"\r\n'
        f"Content-Length: {size}\r\n"
        "Connection: close\r\n\r\n"
    )

    duration_secs = 0.0
    total_bytes_sent = 0
    try:
        conn.sendall(headers.encode('utf-8'))
        total_bytes_sent, duration_secs = send_file_stream(conn, filename, stream_buffer)
        return download_name, (counter % 99) + 1, total_bytes_sent, duration_secs

    except OSError as e:
        print("Error: Active webpage download interrupted mid-stream:", e)
        return None, counter, 0, 0.0


def disable_ap():
    """Shuts down the Wi-Fi AP interface cleanly."""
    ap = network.WLAN(network.AP_IF)
    if ap.active():
        ap.active(False)
        print("Wi-Fi Access Point interface disabled.")
