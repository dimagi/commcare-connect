import mimetypes
import os

# Leading characters that make a spreadsheet treat a cell as a formula.
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


class EchoWriter:
    """A Buffer interface that implements write for csv.writer
    and returns back the value passed to write."""

    def write(self, value):
        return value


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


def neutralize_formula(value):
    """Stop a spreadsheet from evaluating a user-supplied cell as a formula (CSV injection).

    Text starting with a formula trigger gets a leading apostrophe; anything else is returned unchanged.
    """
    if isinstance(value, str) and value.startswith(FORMULA_TRIGGERS):
        return f"'{value}"
    return value
