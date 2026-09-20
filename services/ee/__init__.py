"""Capa vieja de Google Earth Engine, la que precede al pipeline mensual.

**No re-exporta nada** desde M.6.1 (`DECISIONS #59`): los cinco nombres que
tenia en `__all__` no los importaba nadie —todos los modulos importan de
`services.ee.ee_client` o `services.ee.ee_indices`— y dos ya no existen.
"""
