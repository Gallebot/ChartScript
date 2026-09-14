"""chartgen - generador automatizado de charts para Clone Hero."""

__version__ = "0.1.0"

RESOLUTION = 192
"""Ticks por negra. 192 es el estandar de Moonscraper/Clone Hero.

No cambiar: el umbral de HOPO automatico del juego (1/12 de negra = 65 ticks)
esta calibrado sobre este valor.
"""

PAD_SECONDS = 2.0
"""Silencio insertado al inicio del audio. El tick 0 del chart corresponde al
inicio del archivo YA con padding, asi que todo timing medido sobre el audio
original hay que desplazarlo por este valor.
"""
