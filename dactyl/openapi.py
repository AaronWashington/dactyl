################################################################################
## OpenAPI Specification Parsing
##
## Parses an OpenAPI spec in YAML or JSON and generates files from it
################################################################################

import jinja2
from copy import deepcopy
from ruamel.yaml.comments import CommentedMap as YamlMap
from ruamel.yaml.comments import CommentedSeq as YamlSeq

from dactyl.common import *
from dactyl.http_constants import HTTP_METHODS, HTTP_STATUS_CODES

DATA_TYPES_SUFFIX = "-data-types"
METHOD_TOC_SUFFIX = "-methods"

TOC_TEMPLATE = "template-openapi_endpoint_toc.md"
ENDPOINT_TEMPLATE = "template-openapi_endpoint.md"
DATA_TYPES_TOC_TEMPLATE = "template-openapi_data_types_toc.md"
DATA_TYPE_TEMPLATE = "template-openapi_data_type.md"


class ApiDef:
    def __init__(self, fname, api_slug=None, extra_fields={},
                template_path=None):
        with open(fname, "r", encoding="utf-8") as f:
            self.swag = yaml.load(f)
        self.deref_swag()

        try:
            self.api_title = self.swag["info"]["title"]
        except IndexError:
            self.api_title = fname.replace(".yml","")+" API (working title)"

        if api_slug is None:
            self.api_slug = slugify(self.api_title)
        else:
            self.api_slug = api_slug

        self.extra_fields = extra_fields

        if template_path is None:
            loader = jinja2.PackageLoader(__name__)
        else:
            loader = jinja2.ChoiceLoader(
                jinja2.FileSystemLoader(template_path),
                jinja2.PackageLoader(__name__)
            )
        self.env = jinja2.Environment(loader=loader)

    def deref(self, ref, add_title=False):
        """Look through the YAML for a specific reference key, and return
        the value that key represents.
        - Raises IndexError if the key isn't found
            in the YAML.
        - add_title: If true, provide a "title" field when the reference
            resolves to an object that doesn't have a "title". The provided
            "title" value is based on the key that contained the reference
        """
        assert len(ref) > 1 and ref[0] == "#" and ref[1] == "/"
        parts = ref[2:].split("/")
        assert len(parts) > 0

        def dig(parts, context):
            key = parts[0].replace("~1", "/").replace("~0", "~") # unescaped
            try:
                key = int(key)
            except:
                pass
            if key not in context.keys():
                raise IndexError(key)

            if len(parts) == 1:
                if add_title and "keys" in dir(context[key]) and \
                                "title" not in context[key].keys():
                    # Give this object a "title" field based on the key to it
                    context[key]["title"] = parts[0]
                return context[key]
            else:
                return dig(parts[1:], context[key])

        return dig(parts, self.swag)

    def deref_swag(self):
        """
        Walk the OpenAPI specification for $ref objects and resolve them to
        the values they reference. Assumes the entire spec is contained in a
        single file.
        """
        print("in deref_swag")
        def deref_yaml(yaml_value):
            if "keys" in dir(yaml_value): # Dictionary-like type
                if "$ref" in yaml_value.keys():
                    # It's a reference; deref it
                    reffed_value = self.deref(yaml_value["$ref"], True)
                    # The referenced object may contain more references, so
                    # resolve those before returning
                    return deref_yaml(reffed_value)
                else:
                    # recurse through each key/value pair looking for refs
                    the_copy = YamlMap()
                    for k,v in yaml_value.items():
                        the_copy[k] = deref_yaml(v)
                    return the_copy
            elif "append" in dir(yaml_value): # List-like type
                # recurse through each item looking for refs
                the_copy = YamlSeq()
                for item in yaml_value:
                    the_copy.append(deref_yaml(item))
                return the_copy
            else: # Probably a basic type
                # base case: return the value
                return yaml_value

        self.swag = deref_yaml(self.swag)


    def render_method_toc(self):
        t = self.env.get_template(TOC_TEMPLATE)
        context = self.new_context()
        context["endpoints"] = self.endpoint_iter()
        return t.render(self.swag, **context)

    def render_data_types_toc(self):
        t = self.env.get_template(DATA_TYPES_TOC_TEMPLATE)
        context = self.new_context()
        context["schemas"] = self.data_type_iter()
        return t.render(self.swag, **context)

    def render_data_type(self, key, schema):
        t = self.env.get_template(DATA_TYPE_TEMPLATE)
        context = self.new_context()
        if "title" not in schema.keys():
            schema["title"] = key
        return t.render(schema, **context)

    def render_endpoint(self, path, method, endpoint):
        t = self.env.get_template(ENDPOINT_TEMPLATE)
        context = self.new_context()
        context["method"] = method
        context["path"] = path
        context["path_params"] = [p for p in endpoint.get("parameters",[]) if p["in"]=="path"]
        context["query_params"] = [p for p in endpoint.get("parameters",[]) if p["in"]=="query"]
        #TODO: header & cookie params??
        return t.render(endpoint, **context)

    def get_endpoint_renderer(self, path, method, endpoint):
        return lambda: self.render_endpoint(path, method, endpoint)

    def get_data_type_renderer(self, key, schema):
        return lambda: self.render_data_type(key, schema)

    def endpoint_iter(self):
        paths = self.swag.get("paths", {})
        for path, path_def in paths.items():
            for method in HTTP_METHODS:
                if method in path_def.keys():
                    endpoint = path_def[method]
                    yield (path, method, endpoint)

    def data_type_iter(self):
        schemas = self.swag.get("components", {}).get("schemas", {})
        for key,schema in schemas.items():
            title = schema.get("title", key)
            yield (title, schema)

    def create_pagelist(self):
        """
        Return an array of pages representing this API, which Dactyl can use
        as it would use a normal list of pages in the config
        """
        pages = []

        # TODO: make all the blurb/category strings template strings that can
        #       be translated and configured

        # add methods table of contents
        toc_page = deepcopy(self.extra_fields)
        toc_page.update({
            "name": self.api_title+" Methods",
            "__md_generator": self.render_method_toc,
            "html": self.api_slug+METHOD_TOC_SUFFIX+".html",
            "blurb": "List of methods/endpoints available in "+self.api_title,
            "category": self.api_title+" Methods",
        })
        pages.append(toc_page)

        # add each endpoint
        for path, method, endpoint in self.endpoint_iter():
            method_page = deepcopy(self.extra_fields)
            method_page.update({
                "name": endpoint["operationId"],
                "__md_generator": self.get_endpoint_renderer(path, method, endpoint),
                "html": self.method_link(path, method, endpoint),
                "blurb": endpoint.get("description", endpoint["operationId"]+" method"),
                "category": self.api_title+" Methods",
            })
            pages.append(method_page)

        # add data types table of contents
        data_types_page = deepcopy(self.extra_fields)
        data_types_page.update({
            "name": self.api_title+" Data Types",
            "__md_generator": self.render_data_types_toc,
            "html": self.api_slug+DATA_TYPES_SUFFIX+".html",
            "blurb": "List of all data types defined for "+self.api_title,
            "category": self.api_title+" Data Types",
        })
        pages.append(data_types_page)

        # add each data type from the components.schemas list
        schemas = self.swag.get("components", {}).get("schemas", {})
        for title, schema in self.data_type_iter():
            data_type_page = deepcopy(self.extra_fields)
            data_type_page.update({
                "name": title,
                "__md_generator": self.get_data_type_renderer(title, schema),
                "html": self.type_link(title),
                "blurb": "Definition of "+title+" data type",
                "category": self.api_title+" Data Types",
            })
            pages.append(data_type_page)

        return pages

    def new_context(self):
        return {
            "api_title": self.api_title,
            "type_link": self.type_link,
            "method_link": self.method_link,
            "HTTP_METHODS": HTTP_METHODS,
            "HTTP_STATUS_CODES": HTTP_STATUS_CODES,
            "spec": self.swag,
        }

    def type_link(self, title):
        return self.api_slug+DATA_TYPES_SUFFIX+"-"+slugify(title.lower())+".html"

    def method_link(self, path, method, endpoint):
        return self.api_slug+"-"+slugify(endpoint["operationId"]+".html")
