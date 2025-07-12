# -*- coding: utf-8 -*-
import os
import sys
import time
import datetime
import json
import configparser
import requests
from ctypes import POINTER, cast, c_ubyte
from threading import Thread

from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Struct import *
from NetSDK.SDK_Enum import *
from NetSDK.SDK_Callback import *

import logging

# --- Global Variables ---
CONFIGURED_CAMERAS = []
g_attach_handle_map = {}
g_ip_to_friendly_name_map = {}
logger = None # Will be initialized in main()
sdk = None # NetClient instance
IMAGE_SAVE_DIR = None # Será definido en main() desde el config.ini

# --- Async Event Sender ---
def send_event_async(payload, image_path):
    """Function to send event data and image in a separate thread."""
    def task():
        db_manager_url = os.getenv('DB_MANAGER_URL', 'http://anpr-db-manager:5001/event')
        try:
            with open(image_path, 'rb') as image_file:
                files = {'image': (os.path.basename(image_path), image_file, 'image/jpeg')}
                form_data = {'event_data': json.dumps(payload)}
                
                logger.debug(f"Sending event for plate {payload.get('PlateNumber')} to {db_manager_url}")
                response = requests.post(db_manager_url, files=files, data=form_data, timeout=15)
                response.raise_for_status()
                logger.info(f"Async send SUCCESS for plate {payload.get('PlateNumber')}. Status: {response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(f"ASYNC SEND FAILED for plate {payload.get('PlateNumber')}: {e}")
        except FileNotFoundError:
            logger.error(f"IMAGE NOT FOUND for async send: {image_path}")
        finally:
            if os.path.exists(image_path):
                os.remove(image_path)
                logger.debug(f"Cleaned up image file: {image_path}")

    thread = Thread(target=task)
    thread.daemon = True
    thread.start()

# Callback for device disconnect
@CB_FUNCTYPE(None, C_LLONG, c_char_p, C_LDWORD)
def disconnect_callback(lLoginHandle, pchDVRIP, dwUser):
    logger.warning(f"Device disconnected: {pchDVRIP.decode('gb2312', 'ignore')}")

# --- FUNCIÓN CALLBACK PARA PROCESAR EVENTOS ---
@CB_FUNCTYPE(None, C_LLONG, C_DWORD, c_void_p, POINTER(c_ubyte), C_DWORD, C_LDWORD, c_int, c_void_p)
def analyzer_data_callback(lAnalyzerHandle, dwAlarmType, pAlarmInfo, pBuffer, dwBufSize, dwUser, nSequence, reserved):
    if dwAlarmType == EM_EVENT_IVS_TYPE.TRAFFICJUNCTION:
        
        alarm_info = cast(pAlarmInfo, POINTER(DEV_EVENT_TRAFFICJUNCTION_INFO)).contents
        
        camera_ip = g_attach_handle_map.get(lAnalyzerHandle, "Unknown IP")
        
        image_filepath = None
        if pBuffer and dwBufSize > 0:
            try:
                plate_number = alarm_info.stTrafficCar.szPlateNumber.decode('gb2312', errors='ignore').strip()
                utc = alarm_info.UTC
                event_time = datetime.datetime(utc.dwYear, utc.dwMonth, utc.dwDay, utc.dwHour, utc.dwMinute, utc.dwSecond)
                time_str = event_time.strftime("%Y%m%d_%H%M%S")
                filename = f"temp_{time_str}_{camera_ip.replace('.', '-')}_{plate_number}.jpg"
                image_filepath = os.path.join(IMAGE_SAVE_DIR, filename)
                with open(image_filepath, "wb") as f:
                    f.write(bytes(pBuffer[:dwBufSize]))
                logger.info(f"Temp image saved to {image_filepath}")
            except Exception as e:
                logger.error(f"ERROR saving image: {e}")
                return
        else:
            logger.warning("No image buffer in event. Cannot send to DB manager.")
            return

        try:
            plate_number = alarm_info.stTrafficCar.szPlateNumber.decode('gb2312', errors='ignore').strip()
            utc = alarm_info.UTC
            event_time = datetime.datetime(utc.dwYear, utc.dwMonth, utc.dwDay, utc.dwHour, utc.dwMinute, utc.dwSecond)
            
            direction_map = {0: "Unknown", 1: "Approaching", 2: "Leaving"}
            driving_direction_code = getattr(alarm_info.stTrafficCar, 'nDirection', 0)
            driving_direction = direction_map.get(driving_direction_code, "Unknown")
            
            log_message = f"[{event_time.strftime('%Y-%m-%d %H:%M:%S')}] [{camera_ip}] Plate Detected: {plate_number} | Direction: {driving_direction}"
            logger.info(log_message)

            
            
            camera_friendly_name = g_ip_to_friendly_name_map.get(camera_ip, camera_ip)

            plate_color = getattr(alarm_info.stTrafficCar, 'szPlateColor', b'').decode('gb2312', 'ignore').strip() or "N/A"
            confidence = getattr(alarm_info.stTrafficCar, 'nConfidence', 0)
            plate_type = getattr(alarm_info.stTrafficCar, 'szPlateType', b'').decode('gb2312', 'ignore').strip() or "N/A"
            vehicle_brand = getattr(alarm_info.stTrafficCar, 'szVehicleSign', b'').decode('gb2312', 'ignore').strip() or "N/A"

            event_payload = {
                "Timestamp": event_time.isoformat(),
                "EventTimeUTC": event_time.isoformat(),
                "PlateNumber": plate_number,
                "EventType": "TrafficJunction",
                "CameraID": camera_friendly_name,
                "VehicleType": getattr(alarm_info.stTrafficCar, 'szVehicleType', b'').decode('gb2312', 'ignore').strip(),
                "VehicleColor": getattr(alarm_info.stTrafficCar, 'szVehicleColor', b'').decode('gb2312', 'ignore').strip(),
                "PlateColor": plate_color,
                "DrivingDirection": driving_direction,
                "VehicleSpeed": getattr(alarm_info.stTrafficCar, 'nSpeed', 0),
                "Lane": getattr(alarm_info.stTrafficCar, 'nLane', 0),
                "VehicleBrand": vehicle_brand,
                "PlateType": plate_type,
                "Confidence": confidence / 100
            }
            send_event_async(event_payload, image_filepath)

        except Exception as e:
            logger.error(f"Error processing plate data or sending event: {e}", exc_info=True)

# --- FUNCIÓN PRINCIPAL ---
def main():
    global logger, sdk, IMAGE_SAVE_DIR, g_ip_to_friendly_name_map

    config = configparser.ConfigParser(interpolation=None)
    config_path = '/app/config.ini'
    if not os.path.exists(config_path):
        alt_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
        if not os.path.exists(alt_config_path):
            # Usar logging básico si el logger principal aún no está configurado
            logging.basicConfig(level=logging.ERROR)
            logging.error(f"CRITICAL: config.ini not found at {config_path} or {alt_config_path}.")
            sys.exit(1)
        config_path = alt_config_path
    config.read(config_path)

    LOG_DIR = config.get('General', 'LogDirectory', fallback='/app/logs')
    os.makedirs(LOG_DIR, exist_ok=True)
    LOG_FILE = os.path.join(LOG_DIR, 'anpr_listener.log')
    
    # CORRECCIÓN: Se lee la ruta del config.ini como en tu código original.
    IMAGE_SAVE_DIR = config.get('Paths', 'ImageDirectory', fallback='/app/anpr_images')
    os.makedirs(IMAGE_SAVE_DIR, exist_ok=True)
    
    LOG_LEVEL_MAP = {
        '0': logging.ERROR, '1': logging.WARNING, '2': logging.INFO, '3': logging.DEBUG
    }
    log_level_str = config.get('General', 'LogLevel', fallback='2')
    log_level = LOG_LEVEL_MAP.get(log_level_str, logging.INFO)
    log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(module)s:%(funcName)s:%(lineno)d] %(message)s')
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setFormatter(log_formatter)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    logger = logging.getLogger(__name__)
    logger.setLevel(log_level)
    for handler in [file_handler, console_handler]:
        logger.addHandler(handler)

    # Raw data logger setup
    raw_data_logger = logging.getLogger('raw_data')
    raw_data_logger.setLevel(logging.DEBUG)
    raw_data_file_handler = logging.FileHandler(os.path.join(LOG_DIR, 'anpr_listener_raw_data.log'))
    raw_data_file_handler.setFormatter(log_formatter)
    raw_data_logger.addHandler(raw_data_file_handler)

    

    logger.info("--- anpr_listener: Starting main function ---")
    
    sdk = NetClient()
    sdk.InitEx(disconnect_callback) # Usar la función de callback de desconexión
    
    logger.info("--- anpr_listener: Loading camera configurations from config.ini ---")
    default_username = config.get('DahuaSDK', 'DefaultUsername').encode()
    default_password = config.get('DahuaSDK', 'DefaultPassword').encode()
    default_port = config.getint('DahuaSDK', 'DefaultPort')

    for section in config.sections():
        if section.startswith('Camera.'):
            if config.getboolean(section, 'Enabled', fallback=False):
                camera_ip = config.get(section, 'IPAddress')
                friendly_name = config.get(section, 'FriendlyName', fallback=camera_ip)
                
                g_ip_to_friendly_name_map[camera_ip] = friendly_name

                CONFIGURED_CAMERAS.append({
                    'IPAddress': camera_ip,
                    'Port': config.getint(section, 'Port', fallback=default_port),
                    'Username': config.get(section, 'Username', fallback=default_username.decode()).encode(),
                    'Password': config.get(section, 'Password', fallback=default_password.decode()).encode(),
                    'FriendlyName': friendly_name,
                    'login_id': 0, 'attach_id': 0
                })
                logger.info(f"Configured camera: {friendly_name} ({camera_ip})")
    
    logger.info(f"--- anpr_listener: Found {len(CONFIGURED_CAMERAS)} enabled cameras ---")

    if not CONFIGURED_CAMERAS:
        logger.warning("No enabled cameras found in config.ini. Exiting.")
        return

    logger.info("--- anpr_listener: Attempting to log in to cameras ---")
    for cam in CONFIGURED_CAMERAS:
        stuInParam = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuInParam.dwSize = sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
        stuInParam.szIP = cam["IPAddress"].encode()
        stuInParam.nPort = cam["Port"]
        stuInParam.szUserName = cam["Username"]
        stuInParam.szPassword = cam["Password"]
        stuInParam.emSpecCap = EM_LOGIN_SPAC_CAP_TYPE.TCP
        stuOutParam = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuOutParam.dwSize = sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)
        login_id, _, error_msg = sdk.LoginWithHighLevelSecurity(stuInParam, stuOutParam)
        
        if login_id != 0:
            cam["login_id"] = login_id
            logger.info(f"Login SUCCESS: {cam['FriendlyName']} ({cam['IPAddress']})")
            attach_id = sdk.RealLoadPictureEx(login_id, 0, EM_EVENT_IVS_TYPE.TRAFFICJUNCTION, 1, analyzer_data_callback, 0, None)
            if attach_id != 0:
                cam["attach_id"] = attach_id
                g_attach_handle_map[attach_id] = cam["IPAddress"]
                logger.info(f"Subscription SUCCESS: {cam['FriendlyName']}")
            else:
                logger.error(f"Subscription FAILED: {cam['FriendlyName']} - Error: {sdk.GetLastError()}")
                sdk.Logout(login_id)
                cam["login_id"] = 0
        else:
            logger.error(f"Login FAILED: {cam['FriendlyName']} ({cam['IPAddress']}) - {error_msg}")

    logger.info("\n--- System is running. Waiting for license plate events. Press Ctrl+C to exit. ---")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n--- Shutting down ---")
    finally:
        for cam in CONFIGURED_CAMERAS:
            if cam["attach_id"] != 0: sdk.StopLoadPic(cam["attach_id"])
            if cam["login_id"] != 0: sdk.Logout(cam["login_id"])
        sdk.Cleanup()
        logger.info("SDK cleaned up. Exiting.")

if __name__ == "__main__":
    main()