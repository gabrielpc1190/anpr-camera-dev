# Prueba de Concepto de Reconocimiento Automático de Matrículas (ANPR)

Este proyecto es una prueba de concepto (PoC) para interactuar con cámaras de red, suscribirse a sus eventos de análisis de video y capturar datos de matrículas de vehículos en tiempo real.

## Características

- **Conexión Múltiple**: Se conecta a varias cámaras de forma simultánea.
- **Reconocimiento de Matrículas (ANPR)**: Escucha y procesa eventos de `TRAFFICJUNCTION` para identificar matrículas.
- **Registro Dual de Eventos**:
    1.  **Log Simple (`anpr_log.txt`)**: Un registro cronológico y legible de cada matrícula detectada.
    2.  **Log Estructurado (`event_packets.log`)**: Un log detallado en formato JSON con todos los datos del evento (matrícula, color del vehículo, velocidad, timestamps, etc.), ideal para análisis o ingesta en otros sistemas.
- **Captura de Evidencia Visual**: Guarda una imagen JPG del vehículo en la carpeta `capturas/` por cada evento detectado.
- **Instalación Automatizada**: Incluye un script (`run_poc.sh`) que gestiona la creación del entorno virtual y la instalación de dependencias.

---

## Requisitos Previos

- Python 3
- El archivo ZIP del SDK del fabricante.

---

## Instalación y Configuración

El proyecto está diseñado para una configuración rápida y automatizada.

1.  **Descargar el SDK**: Consiga el archivo `General_NetSDK_Eng_Python_linux64_IS_V3.060.0000000.0.R.250409.zip` y colóquelo en la raíz del proyecto.

2.  **Configurar Credenciales**: Abra el archivo `anpr_poc.py` y edite las siguientes variables con los datos de sus cámaras:
    - `USER_NAME`
    - `PASSWORD`
    - `PORT`
    - `CAMERAS` (la lista de direcciones IP)

3.  **Ejecutar el Script de Instalación**: Abra una terminal y ejecute el script `run_poc.sh`.
    ```bash
    chmod +x run_poc.sh
    ./run_poc.sh
    ```
    El script se encargará de:
    - Crear un entorno virtual en `.venv/` si no existe.
    - Instalar el SDK desde el archivo `.zip`.
    - Limpiar los archivos de instalación (incluyendo el `.zip` original).
    - Iniciar la aplicación.

---

## Uso

Una vez completada la instalación, simplemente ejecute el script `run_poc.sh` para iniciar la aplicación:

```bash
./run_poc.sh
```

El script se ejecutará en primer plano, mostrando los logs en la terminal. Para detenerlo, presione `Ctrl+C`.

---

## Estructura del Proyecto

```
.
├── anpr_poc.py           # Script principal con la lógica de la aplicación.
├── run_poc.sh            # Script de inicio y gestión del entorno.
├── README.md             # Esta documentación.
├── GEMINI.md             # Resumen del proyecto para el asistente IA.
├── anpr_log.txt          # Log simple de matrículas detectadas.
├── event_packets.log     # Log estructurado (JSON) de eventos.
├── capturas/             # Carpeta donde se guardan las imágenes.
└── .venv/                # Entorno virtual de Python con las dependencias.
```

## Funcionamiento Interno

1.  **Inicialización**: `run_poc.sh` prepara el entorno y ejecuta `anpr_poc.py`.
2.  **Login**: El script se autentica en cada una de las cámaras listadas.
3.  **Suscripción a Eventos**: Se suscribe al evento `TRAFFICJUNCTION` para recibir notificaciones de ANPR.
4.  **Callback**: La función `analyzer_data_callback` se activa con cada evento. Procesa los datos, los escribe en los dos archivos de log y guarda la imagen correspondiente.
5.  **Bucle Principal**: El script se mantiene activo en un bucle para escuchar eventos de forma continua.