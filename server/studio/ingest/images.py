"""Photo intake: accept PNG, JPEG or WebP pictures of the robot, checked by their file signature."""
MAX_IMAGE = 15 * 1024 * 1024


def image_type(payload):
    if payload.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if payload.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if payload[:4] == b'RIFF' and payload[8:12] == b'WEBP':
        return 'image/webp'
    return None


def check_image(payload):
    if len(payload) > MAX_IMAGE:
        raise ValueError('Images must be at most 15 MB')
    kind = image_type(payload)
    if not kind:
        raise ValueError('Upload PNG, JPEG or WebP images')
    return kind
