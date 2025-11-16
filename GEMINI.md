# ANPR Camera System - Documentación del Proyecto

## 1. Resumen del Proyecto

Este proyecto implementa un sistema completo de Reconocimiento Automático de Placas (ANPR) diseñado para operar de forma continua (24/7). Captura eventos de placas de vehículos desde cámaras IP Dahua, procesa los datos, almacena la información y las imágenes en una base de datos, y proporciona una interfaz web para visualizar los eventos.

El sistema está diseñado para ser robusto y resiliente, con mecanismos de recuperación automática ante reinicios del sistema y fallos temporales de conexión con las cámaras.

## 2. Características Principales

* **Captura de Eventos en Tiempo Real**: Escucha activa de eventos de tráfico enviados por cámaras Dahua.
* **Procesamiento Asíncrono**: Los eventos se envían a un servicio de base de datos de forma asíncrona para no perder datos, incluso bajo alta carga.
* **Almacenamiento Persistente**: Guarda información detallada del evento y la imagen de la placa en una base de datos MariaDB.
* **Interfaz Web**: Una sencilla interfaz web para visualizar, filtrar y paginar los eventos de placas capturadas.
* **Orquestación con Docker**: Todos los servicios están contenerizados y gestionados con Docker Compose para un despliegue y escalabilidad sencillos.
* **Resiliencia Mejorada**: El sistema se recupera automáticamente de reinicios y reconecta con cámaras que han perdido la conexión.
* **Captura de Datos Enriquecida**: Además de la placa, el sistema ahora captura:
    * **Tipo de Vehículo Físico**: (ej. "MotorVehicle").
    * **Dirección de Movimiento**: (ej. "Approaching", "Leaving").
    * **Estado de Acceso**: (ej. "Normal Car", "Trust Car").

## 3. Arquitectura del Sistema

El sistema está compuesto por varios microservicios que trabajan en conjunto:

1.  **`anpr-listener`**:
    * Un servicio de Python que se conecta al SDK de Dahua.
    * Se suscribe a los eventos de tráfico (ANPR) de las cámaras configuradas.
    * **Autorreparación**: Incluye un bucle de salud que verifica periódicamente la conexión con cada cámara e intenta reconectar automáticamente si una cámara se reinicia o pierde la conexión.
    * Al recibir un evento, envía los datos y la imagen de forma asíncrona al `anpr-db-manager`.

2.  **`anpr-db-manager`**:
    * Una API de Python (Flask/Gunicorn) que actúa como la única fuente de verdad para la base de datos.
    * Recibe los eventos del `listener`, guarda la imagen en el disco y escribe los metadatos en la base de datos.
    * Expone endpoints para que la interfaz web pueda consultar los datos.

3.  **`anpr-web`**:
    * Una aplicación web de Python (Flask/Gunicorn) que sirve la interfaz de usuario.
    * Actúa como un proxy, consultando los datos del `anpr-db-manager` para mostrarlos al usuario de forma segura.

4.  **`mariadb`**:
    * El servicio de base de datos donde se almacenan todos los eventos.

5.  **`cloudflared-tunnel`**:
    * Un servicio opcional que expone de forma segura la interfaz web a Internet a través de un túnel de Cloudflare.

## 4. Configuración

La configuración del proyecto se divide en dos archivos principales:

* **`.env`**: Gestiona las credenciales y secretos del entorno, como contraseñas de la base de datos y tokens de API.
* **`app/config.ini`**: Gestiona la configuración de la aplicación, como las direcciones IP, nombres y credenciales de cada cámara.

## 5. Uso

El proyecto se gestiona a través de un script de ayuda `setup.sh`.

* **Para iniciar todos los servicios**:
    ```bash
    ./setup.sh start
    ```
* **Para detener todos los servicios**:
    ```bash
    ./setup.sh stop
    ```
* **Para ver los logs en tiempo real**:
    ```bash
    ./setup.sh logs
    ```
* **Para seguir los logs de un servicio específico** (ej. `anpr-listener`):
    ```bash
    ./setup.sh logs anpr-listener
    ```

## 6. Resiliencia del Sistema

El sistema ha sido mejorado para garantizar una operación continua y fiable:

* **Recuperación ante Reinicios**: Gracias a las condiciones `service_healthy` en `docker-compose.yml`, los servicios se inician en el orden correcto, evitando que el `anpr-listener` falle si la base de datos aún no está lista.
* **Reconexión Automática de Cámaras**: El `anpr-listener` ahora comprueba activamente el estado de la conexión con cada cámara cada 60 segundos. Si una cámara se desconecta (por un reinicio o un fallo de red), el servicio intentará reconectarse automáticamente hasta que lo consiga.

## 7. Solución de Problemas (Troubleshooting)

Esta sección documenta los problemas comunes encontrados y sus soluciones.

#### **Problema 1: El servicio deja de funcionar después de un reinicio del sistema.**

* **Síntoma**: El contenedor `anpr-listener` aparece como "Up" en `docker-compose ps`, pero no procesa nuevos eventos y no genera nuevos logs.
* **Causa**: Condición de carrera en el arranque. El `listener` intentaba iniciarse antes de que el `anpr-db-manager` estuviera completamente listo, fallando al conectar y quedando en un estado "zombi".
* **Solución**: Se modificó `docker-compose.yml` para que `anpr-listener` espere a que la condición de salud (`service_healthy`) de `anpr-db-manager` sea exitosa.

#### **Problema 2: El sistema deja de capturar eventos si una cámara se reinicia.**

* **Síntoma**: El log del `anpr-listener` muestra un mensaje de "Device disconnected" pero nunca vuelve a capturar eventos de esa cámara.
* **Causa**: El script no tenía lógica para reintentar la conexión después de una desconexión.
* **Solución**: Se implementó un bucle de autorreparación en `anpr_listener.py` que cada 60 segundos verifica las conexiones caídas e intenta volver a iniciar sesión y suscribirse a los eventos.

#### **Problema 3: La dirección del vehículo, tipo de vehículo u otros datos no se capturan.**

* **Síntoma**: Los campos en la base de datos o en los logs aparecen como "Unknown" o vacíos, incluso si la placa se lee correctamente.
* **Causa 1**: La cámara no está configurada para analizar y enviar estos datos. Es necesario activar las reglas IVS (Intelligent Video System) correspondientes en la interfaz web de la cámara.
* **Causa 2**: El script de Python está buscando un nombre de campo incorrecto en los datos enviados por el SDK.
* **Solución**: Se analizaron los logs de depuración y los archivos del SDK para identificar los nombres de campo correctos (`szDrivingDirection`, `szObjectType`, etc.) y se implementó una lógica de "fallback" en `anpr_listener.py` para asegurar que se capturen los datos de la fuente más fiable disponible.