# Field Investigations

Documented anomalies, configuration questions, or hardware-side issues that need on-site or in-camera-UI verification — out of scope for code changes, but tracked so they aren't forgotten.

---

## 2026-05-17 — Asimetría de detección entre CAM3 y CAM4 (Fase 5)

**Síntoma**: CAM3 (Fase5-Gate-Inside) detecta consistentemente menos vehículos que CAM4 (Fase5-Gate-Outside). Ratio observado: **CAM4 = 2.24× CAM3** en eventos totales desde 2026-05-15 22:30 (147 vs 329 eventos).

Las dos cámaras están en el mismo punto físico viendo entrada y salida de la misma calle. Cada vehículo que entra eventualmente sale → se esperaría volumen similar.

### Descartado por el análisis (NO es la causa)

| Hipótesis | Evidencia que la descarta |
|---|---|
| Pérdida de red / NAT roto | TCP `10.49.9.50:1177` y `:1277` ambos `open` |
| Desconexión SDK persistente | Sin disconnects desde 2026-05-15 20:25 (~2 días estables) |
| Subscription SDK no activa en CAM3 | Eventos llegan intercalados con CAM4; cero horas con "CAM4>0 y CAM3=0" durante tráfico significativo |
| Bug de atribución (closures) | Validado funcionalmente; placas que pasan por ambas (ej. CBN404: 4 in / 10 out) confirman closures correctos |
| Falla del listener en leer placa | % placa vacía similar entre ambas (Inside 16.3%, Outside 11.6%) |
| Sesgo a un tipo de vehículo | Mix Vehicle/NonMotor casi idéntico en ambas |

### Evidencia clave del problema

- **68 placas únicas** detectadas por CAM4 que **nunca** fueron vistas por CAM3 → vehículos que salieron sin que se registrara su entrada.
- En contraste, solo **18 placas únicas** son "solo Inside" (razonable: vehículos parqueados aún dentro).

### Causas más probables (a verificar en sitio)

1. **ROI / línea de detección de CAM3 más restringida** que CAM4 — la zona donde la cámara "decide disparar" es más pequeña.
2. **Ángulo / encuadre físico** que captura menos área útil de la calle (CAM3 puede estar apuntada más alto, más lejos, o con obstrucción parcial).
3. **Iluminación**: si CAM3 ve placas traseras o tiene menos luz nocturna, baja la tasa. Horas con 0 detecciones Inside coinciden con tráfico bajo nocturno (03:00, 18:00 fin de hora).
4. **Sensibilidad del Smart Plan o threshold** diferente entre las dos cámaras.
5. **Vehículos en grupo**: CAM3 pierde un vehículo cuando otro le bloquea momentáneamente, mientras CAM4 los procesa secuencialmente.

### Checklist para la siguiente sesión (verificación en UI web de cada cámara)

Entrar a la UI web de CAM3 (`10.49.9.50:1180` o desde LAN del modem) y CAM4, comparar lado a lado:

- [ ] *Setting → Event → IVS / Smart Plan* — ¿está en "ANPR" o "Traffic" en ambas? ¿Una está accidentalmente en Face Recognition o IVS genérico?
- [ ] *Setting → Event → IVS → reglas* — ¿la línea/zona de detección cubre toda la calle en ambas? ¿Dirección de detección correcta?
- [ ] *Setting → Camera → Conditions → Exposure / Day & Night* — ¿exposición y compensación de luz consistentes? ¿WDR igual?
- [ ] *Setting → Network → Service → Connections* — ¿misma versión de firmware? (puede explicar el `confidence=0` también, ver siguiente sección)
- [ ] **Visualización física**: ver la live view de CAM3 y confirmar que el ROI cubre el espacio que se espera, sin obstrucciones, con buena iluminación.

---

## 2026-05-17 — `confidence = 0` en todas las placas de cámaras Fase 5

**Síntoma secundario** detectado durante la investigación anterior. Todas las filas de `anpr_events` con `camera_id IN (3, 4)` tienen `confidence = 0`, mientras que las cámaras viejas (Cinco Ventanas, Las Brisas) sí reportan valores de confidence.

El listener lee `alarm_info.stTrafficCar.nConfidence` con `getattr(..., 0)` como fallback. Si las cámaras Fase 5 no pueblan ese campo del SDK, llega 0. Probablemente diferente versión de firmware o diferente modelo de cámara.

**No es bug crítico** — la detección funciona, solo el campo numérico de confianza viene 0. Pero impide ordenar/filtrar por confianza si llegamos a necesitarlo.

### A verificar

- [ ] Versión de firmware de CAM3 y CAM4 vs las viejas.
- [ ] ¿El SDK Dahua usado expone `nConfidence` solo en ciertos modelos/firmwares?
- [ ] ¿Hay un campo alternativo en `DEV_EVENT_TRAFFICJUNCTION_INFO` que se pueda leer como fallback?
