#!/bin/bash
# Uploadrr full automation for Unraid
# Processes all media in the seedbox downloads directory.
# Schedule via Unraid User Scripts plugin (every 30 min) or cron.
#
# Prerequisites:
#   - seedbox mounted at /mnt/seedbox (run seedbox_mount script first)
#   - rclone config at /mnt/cache/appdata/arrrs/uploadrr/rclone/rclone.conf
#   - config.yaml configured with TMDb + tracker API keys

CONTAINER="uploadrr_unraid"
DOWNLOADS_DIR="/seedbox/downloads/qbittorrent"
LOG="/mnt/cache/appdata/arrrs/uploadrr/auto.log"

# Ensure seedbox is mounted
if ! mountpoint -q /mnt/seedbox; then
    mkdir -p /mnt/seedbox
    rclone mount seedbox:/home/wildfirebill /mnt/seedbox \
        --config /mnt/cache/appdata/arrrs/uploadrr/rclone/rclone.conf \
        --daemon --vfs-cache-mode writes --allow-other
    sleep 5
fi

echo "$(date): Starting uploadrr automation" >> "$LOG"

docker exec "$CONTAINER" python3 /app/upload.py \
    --full-auto "$DOWNLOADS_DIR" \
    --unattended \
    --skip-dupe-check \
    >> "$LOG" 2>&1

echo "$(date): Finished uploadrr automation" >> "$LOG"