#!/bin/bash

INTERFACE="wlan0"
LOG_FILE="wifi_log_$(date +%Y%m%d_%H%M%S).csv"
INTERVAL=1  # sekund mezi měřeními

echo "timestamp,signal_dbm,quality,bitrate,connected" > "$LOG_FILE"
echo "Loguji do: $LOG_FILE (Ctrl+C pro stop)"

while true; do
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    
    IW_OUTPUT=$(iw dev "$INTERFACE" link 2>&1)
    
    if echo "$IW_OUTPUT" | grep -q "Not connected"; then
        echo "$TIMESTAMP,,,, 0" | tee -a "$LOG_FILE"
        echo "[$TIMESTAMP] ❌ ODPOJENO"
    else
        SIGNAL=$(echo "$IW_OUTPUT" | grep "signal" | awk '{print $2}')
        BITRATE=$(echo "$IW_OUTPUT" | grep "tx bitrate" | awk '{print $3, $4}')
        
        # Quality z iwconfig (0-70)
        QUALITY=$(iwconfig "$INTERFACE" 2>/dev/null | grep "Link Quality" | \
            awk -F'=' '{print $2}' | awk '{print $1}')
        
        echo "$TIMESTAMP,$SIGNAL,$QUALITY,$BITRATE,1" | tee -a "$LOG_FILE"
        echo "[$TIMESTAMP] 📶 Signál: ${SIGNAL} dBm | Kvalita: $QUALITY | Bitrate: $BITRATE"
    fi
    
    sleep "$INTERVAL"
done