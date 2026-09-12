# SPDX-License-Identifier: MIT
"""
constants.py
------------
Shared Poser channel-name constants used across parsers and importers.

Channel names appear verbatim in CR2/PP2/PZ2 token streams.  Centralising
them here avoids independent frozenset definitions across multiple modules.
"""

ROTATE_CHANNELS: frozenset = frozenset({
    'rotateX',
    'rotateY',
    'rotateZ',
    'rotate',
})

TRANSLATE_CHANNELS: frozenset = frozenset({
    'translateX',
    'translateY',
    'translateZ',
    'translate',
})

SCALE_CHANNELS: frozenset = frozenset({
    'scaleX',
    'scaleY',
    'scaleZ',
    'scale',
})

# All transform channels (rotate + translate + scale), used as a dispatch guard
# and for PZ2 transform-value detection.
TRANSFORM_CHANNELS: frozenset = ROTATE_CHANNELS | TRANSLATE_CHANNELS | SCALE_CHANNELS

# Ordered axis index for the three translate channels (used in shape key delta
# accumulation).  Only the three axis-specific names are included — 'translate'
# (the uniform channel) has no single axis.
TRANSLATE_AXIS: dict = {
    'translateX': 0,
    'translateY': 1,
    'translateZ': 2,
}
