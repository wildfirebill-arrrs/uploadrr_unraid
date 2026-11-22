#!/bin/bash
# Uploadrr Unraid automation script
# Mounts seedbox via rclone, runs uploadrr, then unmounts.
# Usage: ./unraid_automation.sh "/seedbox/downloads/qbittorrent/movie.mkv" --trackers ZDF BLU
#
# Prerequisites:
#   - rclone installed on Unraid (or run inside the container)
#   - rclone config at /boot/config/rclone/rclone.conf (Unraid) or mounted into container
#   - FUSE enabled

set -euo pipefail

CONTAINER="uploadrr_unraid"
REMOTE_PATH="${1:-/downloads/qbittorrent}"
MOUNT_POINT="/mnt/user/seedbox"
RCLONE_CONF="/mnt/cache/appdata/arrrs/uploadrr/rclone/rclone.conf"

# Ensure mount point exists
mkdir -p "$MOUNT_POINT"

# Mount seedbox if not already mounted
if ! mountpoint -q "$MOUNT_POINT"; then
    echo "Mounting seedbox..."
    rclone mount seedbox:/ "$MOUNT_POINT" \
        --config "$RCLONE_CONF" \
        --daemon \
        --vfs-cache-mode writes \
        --allow-other \
        --dir-cache-time 5m
    sleep 3
fi

# Build uploadrr args (pass through remaining args)
shift || true
ARGS=("$@")

echo "Running uploadrr on $REMOTE_PATH ..."
docker exec "$CONTAINER" python3 /app/upload.py "$REMOTE_PATH" "${ARGS[@]}"

echo "Done. Unmounting (optional)..."
# fusermount -u "$MOUNT_POINT"  # uncomment to unmount after run