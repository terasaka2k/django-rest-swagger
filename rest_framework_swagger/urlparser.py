import os
from django.conf import settings
from django.utils import six
from django.utils.importlib import import_module
from django.core.urlresolvers import RegexURLResolver, RegexURLPattern
from django.contrib.admindocs.views import simplify_regex

from rest_framework.views import APIView

from .apidocview import APIDocView


class UrlParser(object):

    def get_apis(self, patterns=None, urlconf=None, filter_path=None, exclude_namespaces=[]):
        """
        Returns all the DRF APIViews found in the project URLs

        patterns -- supply list of patterns (optional)
        exclude_namespaces -- list of namespaces to ignore (optional)
        """
        if patterns is None and urlconf is not None:
            if isinstance(urlconf, six.string_types):
                urls = import_module(urlconf)
            else:
                urls = urlconf
            patterns = urls.urlpatterns
        elif patterns is None and urlconf is None:
            urls = import_module(settings.ROOT_URLCONF)
            patterns = urls.urlpatterns

        apis = self.__flatten_patterns_tree__(
            patterns,
            filter_path=filter_path,
            exclude_namespaces=exclude_namespaces,
        )
        if filter_path is not None:
            return self.get_filtered_apis(apis, filter_path)

        return apis

    def get_filtered_apis(self, apis, filter_path):
        filtered_list = []

        for api in apis:
            if filter_path in api['path'].strip('/'):
                filtered_list.append(api)

        return filtered_list

    def get_top_level_apis(self, apis):
        """
        Returns the 'top level' APIs (ie. swagger 'resources')

        apis -- list of APIs as returned by self.get_apis
        """

        api_paths = [endpoint['path'].strip('/').split('/{', 1)[0] for endpoint in apis]

        def get_prefix():
            commonprefix = os.path.commonprefix(api_paths)
            components = commonprefix.rsplit('/', 1)
            if len(components) > 1:
                return components[0] + '/'
            return components[0]

        def get_heads(paths):
            return (path.split('/', 1)[0] for path in paths)

        prefix = get_prefix()
        if not prefix:
            heads = get_heads(api_paths)
            resource_paths = set(heads)
        else:
            base_len = len(prefix)
            tails = (path[base_len:] for path in api_paths)
            heads = get_heads(tails)
            resource_paths = set([prefix + head for head in heads])

        return sorted(resource_paths, key=self.__get_last_element__)

    def __get_last_element__(self, paths):
        split_paths = paths.split('/')
        return split_paths[len(split_paths) - 1]

    def __assemble_endpoint_data__(self, pattern, prefix='', filter_path=None):
        """
        Creates a dictionary for matched API urls

        pattern -- the pattern to parse
        prefix -- the API path prefix (used by recursion)
        """
        callback = self.__get_pattern_api_callback__(pattern)

        if callback is None or self.__exclude_router_api_root__(callback):
            return

        path = simplify_regex(prefix + pattern.regex.pattern)

        if filter_path is not None:
            if filter_path not in path:
                return None

        path = path.replace('<', '{').replace('>', '}')

        if self.__exclude_format_endpoints__(path):
            return

        return {
            'path': path,
            'pattern': pattern,
            'callback': callback,
        }

    def __flatten_patterns_tree__(self, patterns, prefix='', filter_path=None, exclude_namespaces=[]):
        """
        Uses recursion to flatten url tree.

        patterns -- urlpatterns list
        prefix -- (optional) Prefix for URL pattern
        """
        pattern_list = []

        for pattern in patterns:
            if isinstance(pattern, RegexURLPattern):
                endpoint_data = self.__assemble_endpoint_data__(pattern, prefix, filter_path=filter_path)

                if endpoint_data is None:
                    continue

                pattern_list.append(endpoint_data)

            elif isinstance(pattern, RegexURLResolver):

                if pattern.namespace in exclude_namespaces:
                    continue

                pref = prefix + pattern.regex.pattern
                pattern_list.extend(self.__flatten_patterns_tree__(
                    pattern.url_patterns,
                    pref,
                    filter_path=filter_path,
                    exclude_namespaces=exclude_namespaces,
                ))

        return pattern_list

    def __get_pattern_api_callback__(self, pattern):
        """
        Verifies that pattern callback is a subclass of APIView, and returns the class
        Handles older django & django rest 'cls_instance'
        """
        if not hasattr(pattern, 'callback'):
            return

        if (hasattr(pattern.callback, 'cls') and
                issubclass(pattern.callback.cls, APIView) and
                not issubclass(pattern.callback.cls, APIDocView)):

            return pattern.callback.cls

        elif (hasattr(pattern.callback, 'cls_instance') and
                isinstance(pattern.callback.cls_instance, APIView) and
                not issubclass(pattern.callback.cls_instance, APIDocView)):

            return pattern.callback.cls_instance

    def __exclude_router_api_root__(self, callback):
        """
        Returns True if the URL's callback is rest_framework.routers.APIRoot
        """
        if callback.__module__ == 'rest_framework.routers':
            return True

        return False

    def __exclude_format_endpoints__(self, path):
        """
        Excludes URL patterns that contain .{format}
        """
        if '.{format}' in path:
            return True

        return False


test_paths = [
    {'path': '/top'},
    {'path': '/doc/index'},
    {'path': '/api/v1/echo'},
    {'path': '/api/{version}/'},
    {'path': '/api/{version}/echo'},
    {'path': '/api/v{version}/'},
    {'path': '/api/v{version}/echo/'},
    {'path': '/api/v{version}/echo/{x}'},
]


def powerset(iterable):
    "powerset([1,2,3]) --> () (1,) (2,) (3,) (1,2) (1,3) (2,3) (1,2,3)"
    import itertools
    s = list(iterable)
    return itertools.chain.from_iterable(itertools.combinations(s, r) for r in range(len(s)+1))


import pytest

@pytest.mark.parametrize('apis', powerset(test_paths))
def test_resources(apis):
    urlparser = UrlParser()

    top_apis1 = urlparser.get_top_level_apis(apis)
    top_apis2 = urlparser.simply_get_top_level_apis(apis)

    assert top_apis1 == top_apis2


def test_get_top_level_apis():
    urlparser = UrlParser()

    def check_apis(paths, expected_apis):
        apis = [{'path': p} for p in paths]

        top_level_apis = urlparser.get_top_level_apis(apis)
        assert top_level_apis == expected_apis

        top_level_apis = urlparser.simply_get_top_level_apis(apis)
        assert top_level_apis == expected_apis

    check_apis(
        [
            '/doc/index',
            '/api/{version}/echo'
        ],
        ['api', 'doc']
    )

    check_apis(
        [
            '/api/echo'
        ],
        ['api/echo']
    )

    check_apis(
        [
            '/api/{version}/echo'
        ],
        ['api']
    )

    check_apis(
        [
            '/api/{version}/'
            '/api/{version}/echo'
        ],
        ['api']
    )
    check_apis(
        [
            '/api/{version}/'
            '/api/{version}/echo'
            '/api/v{number}/echo'
        ],
        ['api']
    )

    check_apis(
        [
            '/api/{version}/'
            '/api/{version}/echo'
            '/api/v{number}/'
            '/api/v{number}/echo'
        ],
        ['api']
    )

    check_apis(
        [
            '/api/v{number}/',
            '/api/v{number}/echo'
        ],
        ['api/v{number}']
    )

    check_apis(
        [
            '/api/v{number}/echo'
        ],
        ['api/v{number}/echo']
    )
