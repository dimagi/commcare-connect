from django.urls import get_resolver


def list_urls():
    urls = []

    def extract(patterns, prefix=""):
        for pattern in patterns:
            if hasattr(pattern, "url_patterns"):  # included URLConf
                extract(pattern.url_patterns, prefix + str(pattern.pattern))
            else:
                path = prefix + str(pattern.pattern)
                view = pattern.callback
                methods = []

                # Class-based view
                if hasattr(view, "view_class"):
                    methods = [m.upper() for m in view.view_class.http_method_names if m != "options"]
                # Function-based view with @require_http_methods
                elif hasattr(view, "view_class") is False and hasattr(view, "methods"):
                    methods = [m.upper() for m in view.methods]
                # Function-based view without decorators defaults to GET
                else:
                    methods = ["GET"]

                urls.append((path, methods))

    resolver = get_resolver()
    extract(resolver.url_patterns)
    return urls


# Print
for path, methods in list_urls():
    print(f"{methods} -> {path}")
