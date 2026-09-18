"""El ROI desde el payload de Geocore (M.4.2)."""

import ee


def normalizar_coordenadas(coordinates: object) -> tuple[object, bool]:
    """Traduce el payload de Geocore a coordenadas de GeoJSON.

    Devuelve ``(coordenadas, es_multipoligono)``. **Función pura**: no toca GEE,
    así que se puede probar sin autenticarse. Construir una ``ee.Geometry`` exige
    ``ee.Initialize()``, porque las clases de geometría se generan a partir del
    catálogo de algoritmos del servidor.

    Está separada de :func:`coords_to_geometry` porque es **el contrato con
    Geocore**, y es la parte que se rompe en silencio: Geocore manda
    ``CoordinateDto``, o sea ``[{lat, lng}]``, y GeoJSON quiere ``[lng, lat]``.
    Invertir el orden no produce un error, produce un ROI en otro lugar del
    planeta.
    """
    # Lista de diccionarios: el `CoordinateDto` de C#, que puede venir en
    # camelCase o en PascalCase según cómo esté configurado el serializador.
    if (
        isinstance(coordinates, list)
        and len(coordinates) > 0
        and isinstance(coordinates[0], dict)
    ):
        anillo = []
        for c in coordinates:
            lng = c.get("lng") if c.get("lng") is not None else c.get("Lng")
            lat = c.get("lat") if c.get("lat") is not None else c.get("Lat")
            anillo.append([lng, lat])
        # GEE rechaza un polígono abierto, y Geocore no garantiza cerrarlo.
        if len(anillo) > 0 and anillo[0] != anillo[-1]:
            anillo.append(anillo[0])
        return [anillo], False

    # Un MultiPolygon tiene un nivel de anidamiento más que un Polygon.
    es_multi = (
        isinstance(coordinates, list)
        and len(coordinates) > 0
        and isinstance(coordinates[0], list)
        and len(coordinates[0]) > 0
        and isinstance(coordinates[0][0], list)
        and len(coordinates[0][0]) > 0
        and isinstance(coordinates[0][0][0], list)
    )
    return coordinates, es_multi


def coords_to_geometry(coordinates: object) -> ee.Geometry:
    """Arma la ``ee.Geometry`` del ROI. Requiere ``ee.Initialize()`` previo."""
    coords, es_multi = normalizar_coordenadas(coordinates)
    if es_multi:
        return ee.Geometry.MultiPolygon(coords)
    return ee.Geometry.Polygon(coords)
