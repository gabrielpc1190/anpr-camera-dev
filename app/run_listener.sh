#!/bin/bash
# Este script automatiza la configuración del entorno y la ejecución de la PoC de ANPR.
# 1. Crea un entorno virtual si no existe.
# 2. Instala el SDK desde el archivo .whl si no está instalado.
# 3. Limpia los archivos de instalación.
# 4. Ejecuta el script principal de Python.

# Salir inmediatamente si un comando falla
set -e

# --- Configuración ---
SDK_ZIP_NAME="General_NetSDK_Eng_Python_linux64_IS_V3.060.0000000.0.R.250409.zip"
VENV_DIR=".venv"
PYTHON_SCRIPT="anpr_poc.py"
SDK_PACKAGE_NAME="NetSDK"

# --- Lógica del Script ---

echo "--- Iniciando la PoC de ANPR ---"

# 1. Configuración del Entorno Virtual
if [ ! -d "$VENV_DIR" ]; then
    echo "Creando entorno virtual en '$VENV_DIR'..."
    python3 -m venv "$VENV_DIR"
else
    echo "El entorno virtual ya existe."
fi

# Rutas a los ejecutables del entorno virtual
VENV_PIP="$VENV_DIR/bin/pip"
VENV_PYTHON="$VENV_DIR/bin/python3"

# 2. Instalación del SDK (si es necesario)
if $VENV_PIP show "$SDK_PACKAGE_NAME" &> /dev/null; then
    echo "El paquete del SDK '$SDK_PACKAGE_NAME' ya está instalado. Saltando instalación."
else
    echo "El paquete del SDK no se ha encontrado. Intentando instalar desde '$SDK_ZIP_NAME'..."
    
    if [ ! -f "$SDK_ZIP_NAME" ]; then
        echo "ERROR: El archivo ZIP del SDK '$SDK_ZIP_NAME' no se encuentra."
        echo "Por favor, descárguelo y colóquelo en el directorio raíz del proyecto."
        exit 1
    fi

    # Crear un directorio temporal para la descompresión
    TEMP_DIR=$(mktemp -d)
    echo "Descomprimiendo el SDK en una carpeta temporal..."
    unzip -q "$SDK_ZIP_NAME" -d "$TEMP_DIR"

    # Encontrar el archivo .whl
    WHL_FILE=$(find "$TEMP_DIR" -name "*.whl" | head -n 1)
    if [ -z "$WHL_FILE" ]; then
        echo "ERROR: No se pudo encontrar un archivo .whl en el ZIP del SDK."
        rm -rf "$TEMP_DIR" # Limpiar antes de salir
        exit 1
    fi

    echo "Instalando el SDK desde el archivo wheel..."
    "$VENV_PIP" install "$WHL_FILE"

    echo "Limpiando archivos de instalación..."
    rm -rf "$TEMP_DIR"
    rm -f "$SDK_ZIP_NAME"
    echo "SDK instalado y archivos limpiados."
fi

# 3. Ejecución del Script Principal
echo "----------------------------------------------------"
echo "Todo listo. Ejecutando el script '$PYTHON_SCRIPT'..."
echo "Presione Ctrl+C para detener la aplicación."
echo "----------------------------------------------------"

"$VENV_PYTHON" "$PYTHON_SCRIPT"