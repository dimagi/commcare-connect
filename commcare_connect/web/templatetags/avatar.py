from django import template

register = template.Library()


def _calculate_initials_internal(name):
    if not name:
        # Guest
        return "GU"

    parts = name.split()
    initials = ""
    if len(parts) >= 2 and parts[0] and parts[-1]:
        initials = (parts[0][0] + parts[-1][0]).upper()
    elif len(parts) == 1 and parts[0]:
        if len(parts[0]) >= 2:
            initials = parts[0][:2].upper()
        else:
            initials = parts[0][0].upper()
    elif name:
        if len(name) >= 2:
            initials = name[:2].upper()
        else:
            initials = name[0].upper()

    return initials if initials else "GU"


@register.inclusion_tag("tailwind/components/avatar.html")
def user_avatar(user, size="small", color_classes="bg-brand-mango text-white"):
    display_name = ""
    if user:
        if getattr(user, "name", None):
            display_name = user.name
        elif hasattr(user, "first_name") and hasattr(user, "last_name") and (user.first_name or user.last_name):
            display_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        elif hasattr(user, "username"):
            display_name = user.username

    initials_text = _calculate_initials_internal(display_name)
    if not display_name:
        display_name = initials_text if initials_text != "GU" else "User"

    if size == "large":
        size_classes = "w-16 h-16 text-xl font-bold tracking-widest"
    else:
        size_classes = "w-10 h-10 text-xs font-medium tracking-wider"

    return {
        "initials_text": initials_text,
        "display_name": display_name,
        "size_classes": size_classes,
        "color_classes": color_classes,
    }
