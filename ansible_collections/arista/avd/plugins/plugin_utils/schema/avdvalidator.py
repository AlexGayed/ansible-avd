# Copyright (c) 2023-2024 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
from collections import ChainMap

from ansible_collections.arista.avd.plugins.plugin_utils.errors import AristaAvdError
from ansible_collections.arista.avd.plugins.plugin_utils.schema.refresolver import create_refresolver
from ansible_collections.arista.avd.plugins.plugin_utils.utils import get_all

try:
    import jsonschema
    import jsonschema._types
    import jsonschema._validators
    import jsonschema.protocols
    import jsonschema.validators
except ImportError as imp_exc:
    JSONSCHEMA_IMPORT_ERROR = imp_exc
else:
    JSONSCHEMA_IMPORT_ERROR = None


def _primary_key_validator(validator, primary_key: str, instance: list, schema: dict):
    if not validator.is_type(primary_key, "str"):
        return

    if not validator.is_type(instance, "list"):
        return

    if not all(validator.is_type(element, "dict") for element in instance):
        return

    if not all(element.get(primary_key) is not None for element in instance):
        yield jsonschema.ValidationError(f"Primary key '{primary_key}' is not set on all items as required.")

    if len(set(element.get(primary_key) for element in instance)) < len(instance):
        yield jsonschema.ValidationError(f"Values of Primary key '{primary_key}' are not unique as required.")


def _keys_validator(validator, keys: dict, instance: dict, schema: dict):
    """
    This function validates each key with the relevant subschema
    It also includes various child key validations,
    which can only be implemented with access to the parent "keys" instance.
    - Expand dynamic_keys
    - Validate "allow_other_keys" (default is false)
    - Validate "required" under child keys
    - Expand "dynamic_valid_values" under child keys (don't perform validation)
    """
    if not validator.is_type(instance, "object"):
        return

    # Compile schema_dynamic_keys and add to "dynamic_keys"
    schema_dynamic_keys = schema.get("dynamic_keys", {})
    dynamic_keys = {}
    for dynamic_key, childschema in schema_dynamic_keys.items():
        resolved_keys = get_all(instance, dynamic_key)
        for resolved_key in resolved_keys:
            dynamic_keys.setdefault(resolved_key, childschema)

    all_keys = ChainMap(keys, dynamic_keys)

    # Validation of "allow_other_keys"
    if not schema.get("allow_other_keys", False):
        # Check that instance only contains the schema keys
        invalid_keys = ", ".join([key for key in instance if key not in all_keys and key[0] != "_"])
        if invalid_keys:
            yield jsonschema.ValidationError(f"Unexpected key(s) '{invalid_keys}' found in dict.")

    # Run over child keys and check for required and update child schema with dynamic valid values before
    # descending into validation of child schema.
    for key, childschema in all_keys.items():
        if instance.get(key) is None:
            # Validation of "required" on child keys
            if childschema.get("required"):
                yield jsonschema.ValidationError(f"Required key '{key}' is not set in dict.")

            # Skip further validation since there is nothing to validate.
            continue

        # Expand "dynamic_valid_values" in child schema and add to "valid_values"
        if "dynamic_valid_values" in childschema:
            childschema.setdefault("valid_values", []).extend(get_all(instance, childschema["dynamic_valid_values"]))

        # Perform regular validation of the child schema.
        yield from validator.descend(
            instance[key],
            childschema,
            path=key,
            schema_path=key,
        )


def _dynamic_keys_validator(validator, dynamic_keys: dict, instance: dict, schema: dict):
    """
    This function triggers the regular "keys" validator in case only dynamic_keys is set.
    """
    if "keys" not in schema:
        yield from _keys_validator(validator, {}, instance, schema)


def _ref_validator(validator, ref, instance: dict, schema: dict):
    raise NotImplementedError("$ref must be resolved before using AvdValidator")


def _valid_values_validator(validator, valid_values, instance, schema: dict):
    """
    This function validates if the instance conforms to the "valid_values"
    """
    if instance not in valid_values:
        yield jsonschema.ValidationError(f"'{instance}' is not one of {valid_values}")


def _is_dict(validator, instance):
    return isinstance(instance, (dict, ChainMap))


class AvdValidator:
    def __new__(cls, schema, store):
        """
        AvdSchemaValidator is used to validate AVD Data.
        It uses a combination of our own validators and builtin jsonschema validators
        mapped to our own keywords.
        We have extra type checkers not covered by the AVD_META_SCHEMA (array, boolean etc)
        since the same TypeChecker is used by the validators themselves.
        """
        if JSONSCHEMA_IMPORT_ERROR:
            raise AristaAvdError('Python library "jsonschema" must be installed to use this plugin') from JSONSCHEMA_IMPORT_ERROR

        ValidatorClass = jsonschema.validators.create(
            meta_schema=store["avd_meta_schema"],
            validators={
                "$ref": _ref_validator,
                "type": jsonschema._validators.type,
                "max": jsonschema._validators.maximum,
                "min": jsonschema._validators.minimum,
                "valid_values": _valid_values_validator,
                "format": jsonschema._validators.format,
                "max_length": jsonschema._validators.maxLength,
                "min_length": jsonschema._validators.minLength,
                "pattern": jsonschema._validators.pattern,
                "items": jsonschema._validators.items,
                "primary_key": _primary_key_validator,
                "keys": _keys_validator,
                "dynamic_keys": _dynamic_keys_validator,
            },
            type_checker=jsonschema.TypeChecker(
                {
                    "any": jsonschema._types.is_any,
                    "array": jsonschema._types.is_array,
                    "boolean": jsonschema._types.is_bool,
                    "integer": jsonschema._types.is_integer,
                    "object": jsonschema._types.is_object,
                    "null": jsonschema._types.is_null,
                    "None": jsonschema._types.is_null,
                    "number": jsonschema._types.is_number,
                    "string": jsonschema._types.is_string,
                    "dict": _is_dict,
                    "str": jsonschema._types.is_string,
                    "bool": jsonschema._types.is_bool,
                    "list": jsonschema._types.is_array,
                    "int": jsonschema._types.is_integer,
                }
            ),
            # version="0.1",
        )

        return ValidatorClass(schema, resolver=create_refresolver(schema, store))
