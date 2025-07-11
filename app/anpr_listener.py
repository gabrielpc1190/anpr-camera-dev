
# -*- coding: utf-8 -*-
import os
import sys
import time
import datetime
import json
import configparser # Added for reading config.ini
import requests # Added for making HTTP POST requests
from ctypes import POINTER, cast, c_ubyte

from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Struct import *
from NetSDK.SDK_Enum import *
from NetSDK.SDK_Callback import *

# Global variable to store camera configurations, will be populated from config.ini
CONFIGURED_CAMERAS = []
g_attach_handle_map = {}


# --- Configuration Loading & Camera Setup Function ---
def load_and_prepare_config():
    global CONFIGURED_CAMERAS, IMAGE_SAVE_DIR, LOG_DIR, SDK_DEBUG_LOG_FILE, PACKET_LOG_FILE

    config = configparser.ConfigParser(interpolation=None)
    CONFIG_FILE_PATH = '/app/config.ini'
    if not os.path.exists(CONFIG_FILE_PATH):
        print(f"CRITICAL: config.ini not found at {CONFIG_FILE_PATH}. Exiting.")
        sys.exit(1)
    config.read(CONFIG_FILE_PATH)

    try:
        IMAGE_SAVE_DIR = config.get('Paths', 'ImageDirectory', fallback='/app/anpr_images')
        LOG_DIR = config.get('General', 'LogDirectory', fallback='/app/logs')
    except configparser.Error as e:
        print(f"Error reading general paths from config.ini: {e}. Using default paths.")
        IMAGE_SAVE_DIR = '/app/anpr_images'
        LOG_DIR = '/app/logs'

    os.makedirs(IMAGE_SAVE_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    SDK_DEBUG_LOG_FILE = os.path.join(LOG_DIR, "netsdk_debug_listener.log")
    PACKET_LOG_FILE = os.path.join(LOG_DIR, "event_packets_listener.log")

    # Load DahuaSDK defaults
    default_username = config.get('DahuaSDK', 'DefaultUsername', fallback='admin')
    default_password = config.get('DahuaSDK', 'DefaultPassword', fallback='') # No sensible default for password
    default_port = config.getint('DahuaSDK', 'DefaultPort', fallback=37777) # Ensure port is int

    loaded_cameras_temp = []
    for section_name in config.sections():
        if section_name.startswith("Camera."):
            if config.getboolean(section_name, 'Enabled', fallback=False):
                cam_key = section_name.split('.', 1)[1] # Get CAM1, CAM2 etc.
                ip_address = config.get(section_name, 'IPAddress', fallback=None)
                if not ip_address:
                    print(f"Warning: IPAddress missing for enabled camera section {section_name}. Skipping.")
                    continue

                friendly_name = config.get(section_name, 'FriendlyName', fallback=cam_key)
                username = config.get(section_name, 'Username', fallback=default_username)
                password = config.get(section_name, 'Password', fallback=default_password)
                port = config.getint(section_name, 'Port', fallback=default_port) # Ensure port is int
                channel = config.getint(section_name, 'Channel', fallback=0) # Default channel 0

                loaded_cameras_temp.append({
                    "key": cam_key, # Store the original key for reference if needed
                    "ip": ip_address,
                    "port": port,
                    "username": username,
                    "password": password,
                    "channel": channel,
                    "friendly_name": friendly_name,
                    "login_id": 0,  # Initialize for SDK use
                    "attach_id": 0 # Initialize for SDK use
                })
                print(f"Loaded enabled camera: {friendly_name} ({ip_address})")
            else:
                print(f"Skipping disabled camera section: {section_name}")

    CONFIGURED_CAMERAS = loaded_cameras_temp
    if not CONFIGURED_CAMERAS:
        print("Warning: No enabled cameras found in configuration. Listener will not connect to any cameras.")


# --- FUNCIÓN CALLBACK PARA PROCESAR EVENTOS ---

@CB_FUNCTYPE(None, C_LLONG, C_DWORD, c_void_p, POINTER(c_ubyte), C_DWORD, C_LDWORD, c_int, c_void_p)
def analyzer_data_callback(lAnalyzerHandle, dwAlarmType, pAlarmInfo, pBuffer, dwBufSize, dwUser, nSequence, reserved):
    """
    Esta función se ejecuta cada vez que la cámara envía un evento de tráfico.
    """
    if dwAlarmType == EM_EVENT_IVS_TYPE.TRAFFICJUNCTION:

        alarm_info = cast(pAlarmInfo, POINTER(DEV_EVENT_TRAFFICJUNCTION_INFO)).contents

        # --- Bloque 1: Logging de campos clave del paquete ---
        try:
            utc = alarm_info.UTC
            event_time = datetime.datetime(utc.dwYear, utc.dwMonth, utc.dwDay, utc.dwHour, utc.dwMinute, utc.dwSecond)
            
            # Safely access attributes from alarm_info.stTrafficCar
            st_traffic_car = alarm_info.stTrafficCar

            plate_number_bytes = getattr(st_traffic_car, 'szPlateNumber', b'')
            plate_number = plate_number_bytes.decode('gb2312', errors='ignore').strip() if plate_number_bytes else "N/A"

            vehicle_color_bytes = getattr(st_traffic_car, 'szVehicleColor', b'')
            vehicle_color = vehicle_color_bytes.decode('gb2312', errors='ignore').strip() if vehicle_color_bytes else "N/A"

            # Assuming szVehicleType was the actual field name from the error log, let's add it safely
            vehicle_type_bytes = getattr(st_traffic_car, 'szVehicleType', b'')
            vehicle_type = vehicle_type_bytes.decode('gb2312', errors='ignore').strip() if vehicle_type_bytes else "N/A"

            vehicle_speed = getattr(st_traffic_car, 'nSpeed', 0) # Default to 0 if not present
            lane = getattr(st_traffic_car, 'nLane', 0) # Default to 0 if not present

            packet_details = {
                "timestamp_capture": datetime.datetime.now().isoformat(),
                "camera_ip": g_attach_handle_map.get(lAnalyzerHandle, "Unknown IP"),
                "event_time_utc": event_time.isoformat(),
                "plate_number": plate_number,
                "vehicle_type": vehicle_type, # Added based on the error log
                "vehicle_color": vehicle_color,
                "vehicle_speed": vehicle_speed,
                "lane": lane
            }

            with open(PACKET_LOG_FILE, "a") as f:
                f.write(json.dumps(packet_details, indent=4) + "\n---\n")

        except Exception as e:
            print(f"!!! ERROR writing to packet log: {e}")

        # --- Bloque 3: Guardado de la imagen ---
        try:
            if pBuffer and dwBufSize > 0:
                plate_number = alarm_info.stTrafficCar.szPlateNumber.decode('gb2312', errors='ignore').strip()
                camera_ip = g_attach_handle_map.get(lAnalyzerHandle, "UnknownIP")

                utc = alarm_info.UTC
                event_time = datetime.datetime(utc.dwYear, utc.dwMonth, utc.dwDay, utc.dwHour, utc.dwMinute, utc.dwSecond)

                # Crear un nombre de archivo único
                time_str = event_time.strftime("%Y%m%d_%H%M%S")
                filename = f"{time_str}_{camera_ip.replace('.', '-')}_{plate_number}.jpg"
                # Use IMAGE_SAVE_DIR read from config
                filepath = os.path.join(IMAGE_SAVE_DIR, filename)

                # Guardar el búfer de la imagen en el archivo
                # Ensure directory exists (it should have been created at startup, but good for robustness)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                with open(filepath, "wb") as f:
                    f.write(pBuffer[:dwBufSize])

                print(f"  -> Image saved to {filepath}")

        except Exception as e:
            print(f"!!! ERROR saving image: {e}")

        # --- Bloque 2: Lógica original de detección de matrículas ---
        try:
            plate_number = alarm_info.stTrafficCar.szPlateNumber.decode('gb2312').strip()
            camera_ip = g_attach_handle_map.get(lAnalyzerHandle, "Unknown IP")

            utc = alarm_info.UTC
            event_time = datetime.datetime(utc.dwYear, utc.dwMonth, utc.dwDay, utc.dwHour, utc.dwMinute, utc.dwSecond)
            time_str = event_time.strftime("%Y-%m-%d %H:%M:%S")

            log_message = f"[{time_str}] [{camera_ip}] Plate Detected: {plate_number}"
            print(log_message) # This will go to Docker logs (stdout)

            # Removed writing to local LOG_FILE as Docker handles stdout logging.
            # If separate file logging is desired for plate detections, it should use LOG_DIR
            # with open(os.path.join(LOG_DIR, "plate_detections.log"), "a") as f:
            #     f.write(log_message + "\n")

        except Exception as e:
            print(f"Error processing plate data: {e}")

        # --- Bloque 4: Enviar datos al anpr_db_manager ---
        try:
            db_manager_url = os.getenv('DB_MANAGER_URL', 'http://anpr-db-manager:5001/event')
            # Construct the payload for anpr_db_manager
            # Ensure all fields expected by anpr_db_manager's /event endpoint and table schema are included.
            # event_time and camera_ip are already defined above.
            # plate_number, vehicle_type, vehicle_color, vehicle_speed, lane are from st_traffic_car block.
            # ImageFilename needs to be the 'filename' variable from the image saving block.

            image_filename_for_db = None
            if pBuffer and dwBufSize > 0: # Check if image was processed
                 # Reconstruct filename as it was created in image saving block.
                 # This assumes plate_number variable from st_traffic_car block is the one used for filename.
                 # And event_time is also from the same scope.
                 # camera_ip is from g_attach_handle_map
                _plate_num_for_fn = getattr(st_traffic_car, 'szPlateNumber', b'').decode('gb2312', errors='ignore').strip() if getattr(st_traffic_car, 'szPlateNumber', b'') else "N_A_PLATE"
                _cam_ip_for_fn = g_attach_handle_map.get(lAnalyzerHandle, "UnknownIP")
                _time_str_for_fn = event_time.strftime("%Y%m%d_%H%M%S")
                image_filename_for_db = f"{_time_str_for_fn}_{_cam_ip_for_fn.replace('.', '-')}_{_plate_num_for_fn}.jpg"


            event_payload = {
                "Timestamp": event_time.isoformat(), # From alarm_info.UTC
                "PlateNumber": plate_number, # From st_traffic_car
                "EventType": "TrafficJunction", # Or derive from dwAlarmType if more specific needed
                "CameraID": g_attach_handle_map.get(lAnalyzerHandle, "Unknown IP"),
                "VehicleType": vehicle_type, # From st_traffic_car
                "VehicleColor": vehicle_color, # From st_traffic_car
                "PlateColor": "N/A", # Example: This field isn't directly in TRAFFICJUNCTION, might need default or separate logic
                "ImageFilename": image_filename_for_db, # Filename from image saving block
                "DrivingDirection": "N/A", # Example: This field isn't directly in TRAFFICJUNCTION
                "VehicleSpeed": vehicle_speed, # From st_traffic_car
                "Lane": lane # From st_traffic_car
            }

            print(f"Sending event data to DB Manager: {event_payload}")
            response = requests.post(db_manager_url, json=event_payload, timeout=10) # 10 second timeout
            response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
            print(f"Event data sent to DB Manager successfully. Status: {response.status_code}")

        except requests.exceptions.RequestException as e:
            print(f"!!! ERROR sending event data to DB Manager: {e}")
        except Exception as e:
            print(f"!!! UNEXPECTED ERROR constructing/sending event data to DB Manager: {e}")


# --- FUNCIÓN PRINCIPAL ---

def main():
    # First, load configurations
    load_and_prepare_config()

    if not CONFIGURED_CAMERAS:
        print("No enabled cameras configured. Exiting listener.")
        return

    print("Initializing SDK...")
    sdk = NetClient()
    
    # Setup SDK logging to use the configured LOG_DIR (paths are now global via load_and_prepare_config)
    log_info = LOG_SET_PRINT_INFO()
    log_info.dwSize = sizeof(LOG_SET_PRINT_INFO)
    log_info.bSetFilePath = 1
    log_path_sdk = SDK_DEBUG_LOG_FILE.encode('gbk')
    log_info.szLogFilePath = log_path_sdk

    sdk.LogOpen(log_info)
    print(f"SDK logging enabled. Log file: {SDK_DEBUG_LOG_FILE}")

    sdk.InitEx(None)
    
    print(f"Connecting to {len(CONFIGURED_CAMERAS)} configured camera(s) and subscribing to traffic events...")

    for cam_config in CONFIGURED_CAMERAS: # Iterate over the dynamically loaded cameras
        stuInParam = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuInParam.dwSize = sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)

        # Use values from cam_config
        stuInParam.szIP = cam_config["ip"].encode('utf-8') # Ensure encoding
        stuInParam.nPort = cam_config["port"]
        stuInParam.szUserName = cam_config["username"].encode('utf-8')
        stuInParam.szPassword = cam_config["password"].encode('utf-8')
        stuInParam.emSpecCap = EM_LOGIN_SPAC_CAP_TYPE.TCP
        
        stuOutParam = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuOutParam.dwSize = sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)

        login_id, _, error_msg = sdk.LoginWithHighLevelSecurity(stuInParam, stuOutParam)
        
        if login_id != 0:
            cam_config["login_id"] = login_id # Store login_id in the cam_config dict
            print(f"  - Login SUCCESS: {cam_config['friendly_name']} ({cam_config['ip']})")
            
            channel = cam_config.get("channel", 0) # Use configured channel, default to 0
            bNeedPicFile = 1
            
            # Pass camera_ip or a unique camera identifier as part of dwUser if needed in callback
            # For now, g_attach_handle_map uses attach_id which is fine.
            attach_id = sdk.RealLoadPictureEx(login_id, channel, EM_EVENT_IVS_TYPE.TRAFFICJUNCTION, bNeedPicFile, analyzer_data_callback, 0, None)
            
            if attach_id != 0:
                cam_config["attach_id"] = attach_id # Store attach_id
                # Use a more robust identifier for the map if friendly_name can be non-unique. IP is safer.
                g_attach_handle_map[attach_id] = cam_config["ip"]
                print(f"  - Subscription SUCCESS: {cam_config['friendly_name']} ({cam_config['ip']})")
            else:
                print(f"  - Subscription FAILED: {cam_config['friendly_name']} ({cam_config['ip']}) - Error: {sdk.GetLastError()}")
                sdk.Logout(login_id)
                cam_config["login_id"] = 0 # Reset login_id on failure
        else:
            print(f"  - Login FAILED: {cam_config['friendly_name']} ({cam_config['ip']}) - {error_msg}")

    if any(c.get("attach_id") for c in CONFIGURED_CAMERAS):
        print(f"\n--- System is running. Monitoring {sum(1 for c in CONFIGURED_CAMERAS if c.get('attach_id'))} camera(s). Press Ctrl+C to exit. ---")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n--- Shutting down ---")
    else:
        print("\n--- No cameras successfully subscribed. Listener will exit. ---")
        # No need to loop if no cameras are active

    # Cleanup logic remains largely the same, but iterates CONFIGURED_CAMERAS
    finally:
        print("--- Cleaning up SDK resources ---")
        for cam_config in CONFIGURED_CAMERAS: # Iterate over the dynamically loaded cameras
            if cam_config.get("attach_id", 0) != 0:
                sdk.StopLoadPic(cam_config["attach_id"])
                print(f"  - Unsubscribed from {cam_config['friendly_name']} ({cam_config['ip']})")
            if cam_config.get("login_id", 0) != 0:
                sdk.Logout(cam_config["login_id"])
                print(f"  - Logged out from {cam_config['friendly_name']} ({cam_config['ip']})")
        sdk.Cleanup()
        print("SDK cleaned up. Exiting.")

if __name__ == "__main__":
    main()
