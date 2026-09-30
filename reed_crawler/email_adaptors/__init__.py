"""One adaptor per mail layout, one module per provider.

A template is a body layout: a `signature` that recognises it and an `adaptor(before, after,
prefix)` that reads one card from the lines around a job link (see
`email_pipeline.job_meta_from_body`). Providers ship several layouts and change them without
notice, so a template is picked by what the body says, not by which label the mail arrived
under. Within a provider the first matching signature wins, so a module lists its specific
layouts before its generic ones; across providers order does not matter.

A new layout is a template in its provider's module; a new provider is a module listed here.
"""
from email_adaptors import (haystack, indeed, job24, jobright, linkedin, talent, totaljobs,
                            welcometothejungle)

TEMPLATES = [template
             for module in (linkedin, indeed, totaljobs, haystack, welcometothejungle, jobright,
                            job24, talent)
             for template in module.TEMPLATES]
