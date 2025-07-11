
# -*- coding: utf-8 -*-
import os
import sys
import time
import datetime
import json
import logging
import configparser
import requests # Added for sending data to anpr_db_manager
from ctypes import POINTER, cast, c_ubyte
from threading import Thread # Added for non-blocking send_event_data

from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Struct import *
from NetSDK.SDK_Enum import *
from NetSDK.SDK_Callback import *

# --- Global Variables ---
g_attach_handle_map = {}
CONFIG_FILE = 'config.ini' # Expect config.ini in the same directory or /app/
DB_MANAGER_URL = os.getenv('DB_MANAGER_URL', 'http://anpr-db-manager:5001/event') # URL for anpr_db_manager

# --- Logging Setup ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(funcName)s: %(message)s')

# Console Handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

# File Handler (will be configured in main after reading config)
file_handler = None


def load_config():
    """Loads configuration from config.ini."""
    config = configparser.ConfigParser(interpolation=None)
    # Try reading from /app/config.ini first (for Docker)
    if not config.read(os.path.join('/app', CONFIG_FILE)):
        # Fallback to local directory (for local testing)
        if not config.read(CONFIG_FILE):
            logger.error(f"Configuration file {CONFIG_FILE} not found in /app or current directory.")
            sys.exit(1)

    cameras = []
    for section in config.sections():
        if section.startswith("CAM"):
            if config.getboolean(section, "Enabled", fallback=False):
                cameras.append({
                    "id": section,
                    "ip": config.get(section, "IPAddress"),
                    "port": config.getint(section, "Port", fallback=37777),
                    "username": config.get(section, "Username").encode(), # SDK expects bytes
                    "password": config.get(section, "Password").encode(), # SDK expects bytes
                    "channel": config.getint(section, "Channel", fallback=0),
                    "login_id": 0, # Placeholder for SDK login handle
                    "attach_id": 0 # Placeholder for SDK event attach handle
                })

    log_file_path = config.get('General', 'LogFile', fallback='/app/logs/anpr_listener.log')
    # Ensure log directory exists
    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)

    global file_handler
    if file_handler: # Remove existing file handler if any (e.g., during a config reload)
        logger.removeHandler(file_handler)
    file_handler = logging.FileHandler(log_file_path)
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)

    logger.info(f"Loaded {len(cameras)} enabled cameras from {CONFIG_FILE}.")
    return cameras, log_file_path

def send_event_data_async(event_data, image_buffer):
    """Sends event data and image to anpr_db_manager in a separate thread."""
    thread = Thread(target=send_event_data, args=(event_data, image_buffer))
    thread.start()

def send_event_data(event_data, image_buffer):
    """
    Sends event data and image (if available) to the anpr_db_manager service.
    """
    try:
        files = {}
        if image_buffer:
            files['image'] = ('event_image.jpg', image_buffer, 'image/jpeg')

        payload = {'event_data': json.dumps(event_data)}

        logger.debug(f"Sending data to {DB_MANAGER_URL}. Payload keys: {payload.keys()}, Files: {files is not None}")
        response = requests.post(DB_MANAGER_URL, files=files, data=payload, timeout=10)
        response.raise_for_status() # Raises an HTTPError for bad responses (4XX or 5XX)
        logger.info(f"Event data for plate {event_data.get('plate_number', 'N/A')} sent successfully to anpr_db_manager. Response: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send event data to anpr_db_manager: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred in send_event_data: {e}", exc_info=True)


# --- FUNCIÓN CALLBACK PARA PROCESAR EVENTOS ---
@CB_FUNCTYPE(None, C_LLONG, C_DWORD, c_void_p, POINTER(c_ubyte), C_DWORD, C_LDWORD, c_int, c_void_p)
def analyzer_data_callback(lAnalyzerHandle, dwAlarmType, pAlarmInfo, pBuffer, dwBufSize, dwUser, nSequence, reserved):
    """
    Esta función se ejecuta cada vez que la cámara envía un evento de tráfico.
    """
    if dwAlarmType == EM_EVENT_IVS_TYPE.TRAFFICJUNCTION:
        alarm_info = cast(pAlarmInfo, POINTER(DEV_EVENT_TRAFFICJUNCTION_INFO)).contents
        camera_ip = g_attach_handle_map.get(lAnalyzerHandle, "Unknown IP")

        try:
            utc = alarm_info.UTC
            event_time = datetime.datetime(utc.dwYear, utc.dwMonth, utc.dwDay,
                                           utc.dwHour, utc.dwMinute, utc.dwSecond, utc.dwMillisecond * 1000) # Include milliseconds
            
            plate_number = alarm_info.stTrafficCar.szPlateNumber.decode('gb2312', errors='ignore').strip()
            vehicle_color = alarm_info.stTrafficCar.szVehicleColor.decode('gb2312', errors='ignore').strip() # Example, map to standard later
            plate_color = alarm_info.stTrafficCar.szPlateColor.decode('gb2312', errors='ignore').strip()     # Example
            vehicle_type = alarm_info.stTrafficCar.szVehicleType.decode('gb2312', errors='ignore').strip() # Example
            
            # Determine driving direction (0: Approach, 1: Leave, 2: Unknown)
            direction_map = {0: "Approach", 1: "Leave", 2: "Unknown"}
            driving_direction = direction_map.get(alarm_info.stTrafficCar.emDrivingDirection, "Unknown")

            event_details = {
                "timestamp_capture": event_time.isoformat(),
                "camera_id": camera_ip, # Or a more specific camera ID from config if available
                "plate_number": plate_number,
                "vehicle_color": vehicle_color,
                "plate_color": plate_color,
                "vehicle_type": vehicle_type,
                "vehicle_speed": alarm_info.stTrafficCar.nSpeed, # km/h
                "lane": alarm_info.stTrafficCar.nLane,
                "event_type": "TrafficJunction", # From dwAlarmType
                "driving_direction": driving_direction
                # Potentially add more fields from alarm_info.stTrafficCar if needed
            }

            logger.info(f"Plate Detected: {plate_number} by {camera_ip} at {event_time.strftime('%Y-%m-%d %H:%M:%S')}")

            image_bytes = None
            if pBuffer and dwBufSize > 0:
                image_bytes = pBuffer[:dwBufSize]
                logger.debug(f"Image captured for plate {plate_number}, size: {dwBufSize} bytes.")
            else:
                logger.warning(f"No image buffer received for plate {plate_number} from {camera_ip}")

            # Send data (including image if available) to anpr_db_manager
            send_event_data_async(event_details, image_bytes)

        except Exception as e:
            logger.error(f"Error processing event from {camera_ip}: {e}", exc_info=True)

# --- FUNCIÓN PRINCIPAL ---
def main():
    global CAMERAS # Allow main to modify the global CAMERAS list if needed for runtime changes

    CAMERAS, _ = load_config() # Load camera configurations

    if not CAMERAS:
        logger.error("No enabled cameras found in configuration. Exiting.")
        sys.exit(1)

    logger.info("Initializing Dahua NetSDK...")
    sdk = NetClient()
    
    # Configure SDK logging (optional, but useful for debugging SDK issues)
    log_info_sdk = LOG_SET_PRINT_INFO()
    log_info_sdk.dwSize = sizeof(LOG_SET_PRINT_INFO)
    log_info_sdk.bSetFilePath = 1
    # It's good practice to use a dedicated log for SDK, possibly configured via config.ini too
    sdk_log_path_str = os.path.join(os.path.dirname(load_config()[1]), "netsdk_debug.log")
    os.makedirs(os.path.dirname(sdk_log_path_str), exist_ok=True)
    sdk_log_path_bytes = sdk_log_path_str.encode('gbk') # SDK might expect gbk for paths
    log_info_sdk.szLogFilePath = sdk_log_path_bytes
    if not sdk.LogOpen(log_info_sdk):
        logger.warning(f"Failed to open SDK log file at {sdk_log_path_str}. Error: {sdk.GetLastError()}")
    else:
        logger.info(f"SDK logging enabled. Log file: {sdk_log_path_str}")

    if not sdk.InitEx(None): # Pass a disconnect callback if needed
        logger.critical(f"SDK InitEx failed. Error: {sdk.GetLastError()}")
        sys.exit(1)
    
    logger.info("SDK Initialized. Connecting to cameras and subscribing to traffic events...")

    for cam_config in CAMERAS:
        stuInParam = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuInParam.dwSize = sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
        stuInParam.szIP = cam_config["ip"].encode() # IP from config
        stuInParam.nPort = cam_config["port"]       # Port from config
        stuInParam.szUserName = cam_config["username"] # Username from config
        stuInParam.szPassword = cam_config["password"] # Password from config
        stuInParam.emSpecCap = EM_LOGIN_SPAC_CAP_TYPE.TCP
        
        stuOutParam = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuOutParam.dwSize = sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)

        login_id, device_info, error_msg = sdk.LoginWithHighLevelSecurity(stuInParam, stuOutParam)
        
        if login_id != 0:
            cam_config["login_id"] = login_id
            logger.info(f"  - Login SUCCESS: {cam_config['ip']} (Camera ID: {cam_config['id']})")
            
            channel = cam_config["channel"] # Channel from config
            bNeedPicFile = 1 # Request picture file
            
            # The last parameter (dwUser) can be used to pass custom data (like camera_id) to the callback
            # For simplicity, g_attach_handle_map is used here.
            attach_id = sdk.RealLoadPictureEx(login_id, channel, EM_EVENT_IVS_TYPE.TRAFFICJUNCTION,
                                              bNeedPicFile, analyzer_data_callback, 0, None)
            
            if attach_id != 0:
                cam_config["attach_id"] = attach_id
                g_attach_handle_map[attach_id] = cam_config["ip"] # Store IP, could also store cam_config['id']
                logger.info(f"  - Subscription SUCCESS for traffic events: {cam_config['ip']} (Attach ID: {attach_id})")
            else:
                logger.error(f"  - Subscription FAILED for {cam_config['ip']}: Error {sdk.GetLastError()}")
                sdk.Logout(login_id)
                cam_config["login_id"] = 0 # Reset login_id
        else:
            # Ensure error_msg is decoded if it's bytes, or handle appropriately
            error_details = error_msg.decode('gbk', errors='ignore') if isinstance(error_msg, bytes) else str(error_msg)
            logger.error(f"  - Login FAILED: {cam_config['ip']} (Camera ID: {cam_config['id']}) - Error: {error_details} (SDK Error Code: {sdk.GetLastError()})")


    active_cameras = [c for c in CAMERAS if c["attach_id"] != 0]
    if not active_cameras:
        logger.error("No cameras successfully subscribed. Exiting.")
        sdk.Cleanup()
        sys.exit(1)

    logger.info(f"\n--- System is running. Monitoring {len(active_cameras)} camera(s). Press Ctrl+C to exit. ---")
    try:
        while True:
            # Keep main thread alive. Could add periodic checks or re-login logic here if needed.
            time.sleep(5)
            # Example: Check if any camera needs re-login (advanced)
            # for cam_config in CAMERAS:
            #     if cam_config["login_id"] != 0 and not sdk.IsConnected(cam_config["login_id"]): # Fictional IsConnected
            #         logger.warning(f"Camera {cam_config['ip']} disconnected. Attempting to re-login...")
            #         # Implement re-login logic
            #         pass

    except KeyboardInterrupt:
        logger.info("\n--- User initiated shutdown (Ctrl+C) ---")
    except Exception as e:
        logger.error(f"An unexpected error occurred in the main loop: {e}", exc_info=True)
    finally:
        logger.info("--- Shutting down services and SDK ---")
        for cam_config in CAMERAS:
            if cam_config.get("attach_id", 0) != 0:
                logger.info(f"  - Unsubscribing from {cam_config['ip']} (Attach ID: {cam_config['attach_id']})")
                sdk.StopLoadPic(cam_config["attach_id"])
            if cam_config.get("login_id", 0) != 0:
                logger.info(f"  - Logging out from {cam_config['ip']}")
                sdk.Logout(cam_config["login_id"])

        logger.info("Cleaning up SDK resources...")
        sdk.Cleanup()
        logger.info("SDK cleaned up. ANPR Listener stopped.")

if __name__ == "__main__":
    # Ensure the script's directory is in PYTHONPATH for imports if run directly
    # sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    main()
