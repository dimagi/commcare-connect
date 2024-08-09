from django.db import models

from commcare_connect.opportunity.models import DeliveryType
from commcare_connect.utils.db import BaseModel, slugify_uniquely


class Program(BaseModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.CharField()
    delivery_type = models.ForeignKey(DeliveryType, on_delete=models.PROTECT)
    budget = models.IntegerField()
    currency = models.CharField(max_length=3)
    start_date = models.DateField()
    end_date = models.DateField()

    def save(self, *args, **kwargs):
        if not self.id:
            self.slug = slugify_uniquely(self.name, self.__class__)
        super().save(*args, **kwargs)
