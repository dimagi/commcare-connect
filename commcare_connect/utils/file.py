import mimetypes
import os


def get_file_extension(file):
    file_extension = None
    if hasattr(file, "content_type") and file.content_type:
        file_extension = mimetypes.guess_extension(file.content_type)
        if file_extension:
            file_extension = file_extension[1:]  # Remove the leading dot

    if not file_extension and hasattr(file, "name"):
        file_name, file_extension = os.path.splitext(file.name)
        file_extension = file_extension.lstrip(".").lower()
    return file_extension.lower() if file_extension else None
