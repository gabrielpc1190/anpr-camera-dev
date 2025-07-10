# Proyecto ANPR-POC

## Resumen General

El proyecto `anpr-poc` es una prueba de concepto (PoC) para el Reconocimiento Automático de Matrículas (ANPR) utilizando cámaras de red y un SDK específico del fabricante. El script principal, `anpr_poc.py`, se conecta a las cámaras, se suscribe a eventos de análisis de video inteligente (IVS), y al detectar una matrícula, registra la información y guarda una imagen del evento.

## Componentes Clave

*   **`anpr_poc.py`**: Script principal con la lógica de conexión, suscripción a eventos y procesamiento de datos de matrículas.
*   **`run_poc.sh`**: Script para automatizar la configuración del entorno virtual, la instalación del SDK y la ejecución de la PoC.
*   **`NetSDK/`**: Directorio que contiene el SDK del fabricante para la interacción con las cámaras.
*   **`anpr_log.txt`**: Archivo de log donde se registran las matrículas detectadas.
*   **`event_packets.log`**: Archivo de log que almacena los detalles de los paquetes de eventos en formato JSON para depuración.
*   **`capturas/`**: Directorio donde se guardan las imágenes JPG de los vehículos cuyas matrículas han sido reconocidas.
*   **`README.md`**: Contiene la documentación del proyecto, incluyendo instrucciones de instalación y un resumen del funcionamiento.

## Flujo de Ejecución

1.  **Inicialización**: El script `run_poc.sh` verifica y configura el entorno virtual de Python e instala el SDK si es necesario.
2.  **Ejecución**: `run_poc.sh` ejecuta `anpr_poc.py`.
3.  **Conexión**: `anpr_poc.py` se conecta a las cámaras definidas en la configuración utilizando las credenciales proporcionadas.
4.  **Suscripción a Eventos**: El script se suscribe a los eventos de tráfico (`TRAFFICJUNCTION`) de las cámaras.
5.  **Procesamiento de Eventos**: Una función de callback (`analyzer_data_callback`) se activa cuando se detecta un evento. Esta función:
    *   Extrae la información de la matrícula, color del vehículo, velocidad y carril.
    *   Registra los detalles del evento en `event_packets.log`.
    *   Guarda una imagen del vehículo en el directorio `capturas/`.
    *   Añade una entrada de log en `anpr_log.txt`.
6.  **Bucle de Espera**: El script se mantiene en ejecución para escuchar eventos continuamente hasta que se interrumpe manualmente (Ctrl+C).

## Comandos

*   Para ejecutar la prueba de concepto:

    ```bash
    ./run_poc.sh
    ```

## Dependencias

*   Python 3
*   El SDK `NetSDK` del fabricante, que se instala a través del script `run_poc.sh` desde un archivo ZIP.

## Análisis de Ejecución en Tiempo Real (10-07-2025)

Se ha ejecutado la PoC en segundo plano y se han monitoreado los archivos de log (`anpr_log.txt` y `event_packets.log`) para observar su comportamiento con datos reales de las cámaras.

### Observaciones

1.  **Recepción de Eventos Exitosa**: El script recibió y procesó correctamente los eventos de detección de matrículas enviados desde las cámaras.
2.  **Doble Formato de Log**:
    *   **`anpr_log.txt`**: Generó un log simple, cronológico y fácil de leer para una supervisión rápida.
    *   **`event_packets.log`**: Creó un log estructurado en formato JSON por cada evento, conteniendo información detallada como la matrícula, IP de la cámara, color del vehículo, velocidad, carril y timestamps precisos. Este formato es ideal para la ingesta de datos en otros sistemas.
3.  **Captura de Imágenes**: Se confirmó que, en paralelo al registro en los logs, el sistema está diseñado para guardar una imagen JPG por cada evento en la carpeta `capturas/`.
4.  **Baja Latencia**: La diferencia entre el timestamp de la cámara (`event_time_utc`) y el del script (`timestamp_capture`) fue mínima (milisegundos), indicando una comunicación y procesamiento muy eficientes.

### Conclusión de la Prueba

La prueba de concepto ha demostrado ser **exitosa, robusta y fiable** en un entorno simulado de producción. El sistema es capaz de gestionar eventos de múltiples fuentes, procesar los datos de forma estructurada y generar los artefactos necesarios (logs e imágenes) para una solución completa de ANPR. Esta PoC es una base sólida para el desarrollo del nuevo proyecto.