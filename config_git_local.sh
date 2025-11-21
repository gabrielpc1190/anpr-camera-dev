#!/bin/bash

# ==============================================================================
# SCRIPT DE CONFIGURACIÓN DE GIT LOCAL Y SSH POR REPOSITORIO
# Autor: Programador Gabriel (Asistente AI)
# Propósito: Aislar identidad y credenciales por proyecto.
# ==============================================================================

set -e # Detener ejecución ante cualquier error

# 1. Verificación de Entorno
echo ">>> Verificando entorno..."

if [ ! -d ".git" ]; then
    echo "❌ ERROR CRÍTICO: No se detecta un repositorio Git en este directorio."
    echo "    Por favor, ejecute este script dentro de la raíz del proyecto."
    exit 1
fi

# Verificar versión de Git para soporte de core.sshCommand
GIT_VERSION=$(git --version | awk '{print $3}')
echo "ℹ️  Versión de Git detectada: $GIT_VERSION"
# Nota: Asumimos compatibilidad, si es muy antiguo (<2.10) fallará la config local de sshCommand.

# 2. Recolección de Datos (Sin suposiciones)
echo "----------------------------------------------------------------"
echo "Ingrese los datos para ESTE repositorio específico."
echo "----------------------------------------------------------------"

read -p "Nombre de Usuario (para commits): " GIT_USER
read -p "Email (para commits): " GIT_EMAIL
read -p "Nombre único para la llave SSH (ej: id_proyecto_x): " KEY_NAME

if [[ -z "$GIT_USER" || -z "$GIT_EMAIL" || -z "$KEY_NAME" ]]; then
    echo "❌ ERROR: Todos los campos son obligatorios para mantener la integridad."
    exit 1
fi

# 3. Configuración de Identidad Local
echo ">>> Configurando identidad local..."
git config --local user.name "$GIT_USER"
git config --local user.email "$GIT_EMAIL"

# 4. Gestión de Llaves SSH
KEY_DIR="$HOME/.ssh/repo_keys"
KEY_PATH="$KEY_DIR/$KEY_NAME"

echo ">>> Verificando directorio de llaves aisladas ($KEY_DIR)..."
if [ ! -d "$KEY_DIR" ]; then
    mkdir -p "$KEY_DIR"
    chmod 700 "$KEY_DIR"
    echo "✅ Directorio creado."
fi

if [ -f "$KEY_PATH" ]; then
    echo "⚠️  ADVERTENCIA: Ya existe una llave con ese nombre en $KEY_PATH."
    read -p "¿Desea usar la existente (s) o abortar (n)? " OVERWRITE
    if [[ "$OVERWRITE" != "s" ]]; then
        echo "🛑 Operación abortada por seguridad."
        exit 1
    fi
else
    echo ">>> Generando nueva llave SSH (ED25519)..."
    # Se usa -N "" para passphrase vacía por defecto (ajustable) y -C comentario
    ssh-keygen -t ed25519 -C "$GIT_EMAIL" -f "$KEY_PATH" -N ""
    echo "✅ Llave generada."
fi

# 5. Vinculación de Llave al Repositorio
echo ">>> Configurando git local para usar esta llave..."
# Esto configura git para usar un comando SSH específico que apunta a nuestra llave
git config --local core.sshCommand "ssh -i $KEY_PATH -F /dev/null"

echo "================================================================"
echo "✅ CONFIGURACIÓN FINALIZADA CON ÉXITO"
echo "================================================================"
echo "1. Identidad local configurada: $(git config --local user.name) <$(git config --local user.email)>"
echo "2. Llave privada: $KEY_PATH"
echo "3. Llave PÚBLICA (Agregue esto a GitHub/Deploy Keys):"
echo "----------------------------------------------------------------"
cat "${KEY_PATH}.pub"
echo "----------------------------------------------------------------"
