from django import forms, template

register = template.Library()

STEP_INDEX = {"PENDING": 0, "PRINTING": 1, "READY": 2, "COMPLETED": 3, "CANCELLED": -1}


@register.filter
def bs(bound_field, arg=""):
    """Render a BoundField's widget with Bootstrap classes.

    arg: space-separated tokens. A token containing "=" is an HTML attribute (e.g. ``role=switch``);
    any other token is an extra CSS class. Existing widget attrs (accept, step, maxlength...) are kept.
    """
    widget = bound_field.field.widget
    if isinstance(widget, (forms.HiddenInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
        return str(bound_field)

    extra_classes = []
    extra_attrs = {}
    for token in (arg or "").split():
        if "=" in token:
            key, value = token.split("=", 1)
            extra_attrs[key] = value
        else:
            extra_classes.append(token)

    if isinstance(widget, forms.CheckboxInput):
        base_class = "form-check-input"
    elif isinstance(widget, forms.Select):
        base_class = "form-select"
    else:
        base_class = "form-control"

    # as_widget() attrs REPLACE the widget's own class, so merge the existing class in here.
    classes = widget.attrs.get("class", "").split() + [base_class] + extra_classes
    attrs = dict(extra_attrs)
    describedby = []
    auto_id = bound_field.auto_id
    if bound_field.help_text and auto_id:
        describedby.append(f"{auto_id}_help")
    if bound_field.errors:
        classes.append("is-invalid")
        attrs["aria-invalid"] = "true"
        if auto_id:
            describedby.append(f"{auto_id}_errors")
    if describedby:
        attrs["aria-describedby"] = " ".join(describedby)
    attrs["class"] = " ".join(dict.fromkeys(classes))  # de-duplicate, keep order
    return bound_field.as_widget(attrs=attrs)


@register.filter
def step_index(status):
    """Position of an order status in the progress stepper (0..3); -1 for CANCELLED or unknown."""
    return STEP_INDEX.get(status, -1)
