"""Las funciones que el worker le sirve a Inngest.

**Sólo la lista.** Ninguna función se define acá: cada una vive en su módulo, y
esto es el único lugar donde se dice cuáles se registran. Un handler que no entre
en :data:`all_functions` no lo sirve nadie, aunque esté escrito y probado.

Reemplaza a ``services/inngest_handlers.py``, que era la capa vieja: hasta
M.6.2b tenía los handlers a demanda además de la lista. M.6.1 y M.6.2 se llevaron
los suyos, M.6.2b mudó el último al pipeline, y lo que quedaba era esto.

El orden no importa para Inngest; se agrupa por familia para que se lea.
"""

from handlers.cancelaciones import cerrar_altas_canceladas
from handlers.diagnostico import diagnostico_latencia
from handlers.mapa import generate_heatmap_on_demand
from handlers.mes import process_parcela_mes, process_rancho_mes
from handlers.parcela import process_parcela
from handlers.rancho import process_rancho

all_functions = [
    # Altas: los 24 meses de una entidad nueva (M.4.4 y M.4.5).
    process_parcela,
    process_rancho,
    # Cierre de mes: un mes por evento, el que manda Geocore (M.5.3).
    process_parcela_mes,
    process_rancho_mes,
    # A pedido: un índice y un mes (M.6.2b).
    generate_heatmap_on_demand,
    # No las dispara Geocore: una la emite Inngest cuando cancela una corrida
    # (M.4.7), y la otra se dispara a mano para medir la latencia (M.4.10).
    cerrar_altas_canceladas,
    diagnostico_latencia,
]
