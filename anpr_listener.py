
# -*- coding: utf-8 -*-
import os
import sys
import time
import datetime
import json
from ctypes import POINTER, cast, c_ubyte

from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Struct import *
from NetSDK.SDK_Enum import *
from NetSDK.SDK_Callback import *

# --- CONFIGURACIÓN DE CÁMARAS ---
USER_NAME = b"admin"
PASSWORD = b"lb37AhH7zjzODf."
PORT = 37777

CAMERAS = [
    {"ip": "10.45.14.11", "login_id": 0, "attach_id": 0},
    {"ip": "10.45.14.12", "login_id": 0, "attach_id": 0},
]

g_attach_handle_map = {}
LOG_FILE = "anpr.log"
PACKET_LOG_FILE = "event_packets.log"

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
            
            packet_details = {
                "timestamp_capture": datetime.datetime.now().isoformat(),
                "camera_ip": g_attach_handle_map.get(lAnalyzerHandle, "Unknown IP"),
                "event_time_utc": event_time.isoformat(),
                "plate_number": alarm_info.stTrafficCar.szPlateNumber.decode('gb2312', errors='ignore').strip(),
                "vehicle_color": alarm_info.stTrafficCar.szVehicleColor.decode('gb2312', errors='ignore').strip(),
                "vehicle_speed": alarm_info.stTrafficCar.nSpeed,
                "lane": alarm_info.stTrafficCar.nLane
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
                filepath = os.path.join("capturas", filename)

                # Guardar el búfer de la imagen en el archivo
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
            print(log_message)
            
            with open(LOG_FILE, "a") as f:
                f.write(log_message + "\n")

        except Exception as e:
            print(f"Error processing plate data: {e}")

# --- FUNCIÓN PRINCIPAL ---

def main():
    print("Initializing SDK...")
    sdk = NetClient()
    
    log_info = LOG_SET_PRINT_INFO()
    log_info.dwSize = sizeof(LOG_SET_PRINT_INFO)
    log_info.bSetFilePath = 1
    log_path = os.path.join(os.getcwd(), "sdk_debug.log").encode('gbk')
    log_info.szLogFilePath = log_path
    sdk.LogOpen(log_info)
    print(f"SDK logging enabled. Log file: {log_path.decode()}")

    sdk.InitEx(None)
    
    print("Connecting to cameras and subscribing to traffic events...")

    for cam in CAMERAS:
        stuInParam = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuInParam.dwSize = sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
        stuInParam.szIP = cam["ip"].encode()
        stuInParam.nPort = PORT
        stuInParam.szUserName = USER_NAME
        stuInParam.szPassword = PASSWORD
        stuInParam.emSpecCap = EM_LOGIN_SPAC_CAP_TYPE.TCP
        
        stuOutParam = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuOutParam.dwSize = sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)

        login_id, _, error_msg = sdk.LoginWithHighLevelSecurity(stuInParam, stuOutParam)
        
        if login_id != 0:
            cam["login_id"] = login_id
            print(f"  - Login SUCCESS: {cam['ip']}")
            
            channel = 0
            bNeedPicFile = 1 # <--- CAMBIO CLAVE: 1 para solicitar la imagen
            
            attach_id = sdk.RealLoadPictureEx(login_id, channel, EM_EVENT_IVS_TYPE.TRAFFICJUNCTION, bNeedPicFile, analyzer_data_callback, 0, None)
            
            if attach_id != 0:
                cam["attach_id"] = attach_id
                g_attach_handle_map[attach_id] = cam["ip"]
                print(f"  - Subscription SUCCESS: {cam['ip']}")
            else:
                print(f"  - Subscription FAILED: {cam['ip']} - Error: {sdk.GetLastError()}")
                sdk.Logout(login_id)
                cam["login_id"] = 0
        else:
            print(f"  - Login FAILED: {cam['ip']} - {error_msg}")

    print("\n--- System is running. Waiting for license plate events. Press Ctrl+C to exit. ---")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n--- Shutting down ---")
    finally:
        for cam in CAMERAS:
            if cam["attach_id"] != 0:
                sdk.StopLoadPic(cam["attach_id"])
                print(f"  - Unsubscribed from {cam['ip']}")
            if cam["login_id"] != 0:
                sdk.Logout(cam["login_id"])
                print(f"  - Logged out from {cam['ip']}")
        sdk.Cleanup()
        print("SDK cleaned up. Exiting.")

if __name__ == "__main__":
    main()
