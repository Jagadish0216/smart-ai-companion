import random
import time

def get_mock_device_metrics() -> dict:
    """
    Returns simulated Raspberry Pi 5 device metrics.
    TODO: Connect to real psutil/hardware sensors.
    """
    return {
        "device_name": "Raspberry Pi 5",
        "status": "ONLINE",
        "cpu_percent": round(random.uniform(10.0, 45.0), 1),
        "ram_percent": round(random.uniform(40.0, 75.0), 1),
        "ram_used_gb": round(random.uniform(2.0, 4.0), 1),
        "ram_total_gb": 8.0,
        "storage_percent": 32.5,
        "temperature_c": round(random.uniform(40.0, 65.0), 1),
        "network": "CONNECTED",
        "audio_status": "READY",
        "display_status": "READY"
    }
