FROM debian:bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV LIBVA_DRIVER_NAME=iHD

# Set custom sources.list
RUN echo "deb http://deb.debian.org/debian bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb http://deb.debian.org/debian bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb http://security.debian.org/debian-security bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list

# Install dependencies, matching flask_app.py
RUN apt-get update && apt-get install -y \
    python3 python3-venv python3-pip curl nano ffmpeg \
    intel-media-va-driver-non-free vainfo \
    --no-install-recommends && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN useradd -m appuser
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir flask requests

COPY app /app
COPY start.sh /start.sh
COPY init-data.sh /init-data.sh

RUN chmod +x /start.sh /init-data.sh && \
    chown -R appuser:appuser /app && \
    chmod -R u+w /app

USER appuser
CMD ["/init-data.sh"]
