from django.views.generic import TemplateView


class HomeView(TemplateView):
    template_name = "prelogin/home.html"


class ContactView(HomeView):
    template_name = "prelogin/contact.html"


home = HomeView.as_view()
contact = ContactView.as_view()
