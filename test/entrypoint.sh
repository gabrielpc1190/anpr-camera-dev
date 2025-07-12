#!/bin/sh

# Inicia el servidor Flask en segundo plano. El '&' es crucial.
echo "ENTRYPOINT: Starting Flask server in background..."
python anpr_db_manager.py &

# Dale al servidor unos segundos para que se levante.
echo "ENTRYPOINT: Waiting 10 seconds for server to initialize..."
sleep 10

# Ejecuta el script de pruebas, que ahora se conectará a localhost.
echo "ENTRYPOINT: Running test script..."
python test_runner.py

# El resultado del test (código de salida) será el resultado del contenedor.