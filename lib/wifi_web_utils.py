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


def ap_mode(ssid, password):
    """Standard Wi-Fi Access Point setup routine."""
    gc.collect()
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
    print(f"\nConnect to Wi-Fi network: {ssid} \nDownload page at: http://{ip}\n")
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


def disable_ap():
    """Shuts down the Wi-Fi AP interface cleanly."""
    ap = network.WLAN(network.AP_IF)
    if ap.active():
        ap.active(False)
        print("Wi-Fi Access Point interface disabled.")
