FROM debian:bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set custom sources.list with US mirror
RUN rm -f /etc/apt/sources.list.d/* && \
    echo "deb http://ftp.us.debian.org/debian bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb http://ftp.us.debian.org/debian bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb http://security.debian.org/debian-security bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list

# Install dependencies, including FFmpeg and Intel drivers
RUN apt-get update && apt-get install -y \
    python3 python3-venv python3-pip curl nano ffmpeg \
    intel-media-va-driver-non-free libva-utils \
    --no-install-recommends && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN useradd -m appuser
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir flask requests

COPY app /app
COPY start.sh /start.sh

RUN chmod +x /start.sh && \
    chown -R appuser:appuser /app && \
    chmod -R u+w /app

USER appuser
CMD ["/start.sh"]
