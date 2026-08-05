"use strict";
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/index.ts
var src_exports = {};
__export(src_exports, {
  makeZodI18nMap: () => makeZodI18nMap,
  zodI18nMap: () => zodI18nMap
});
module.exports = __toCommonJS(src_exports);
var import_zod = require("zod");
var import_i18next = __toESM(require("i18next"));
var jsonStringifyReplacer = (_, value) => {
  if (typeof value === "bigint") {
    return value.toString();
  }
  return value;
};
function joinValues(array, separator = " | ") {
  return array.map((val) => typeof val === "string" ? `'${val}'` : val).join(separator);
}
var isRecord = (value) => {
  if (typeof value !== "object" || value === null)
    return false;
  for (const key in value) {
    if (!Object.prototype.hasOwnProperty.call(value, key))
      return false;
  }
  return true;
};
var getKeyAndValues = (param, defaultKey) => {
  if (typeof param === "string")
    return { key: param, values: {} };
  if (isRecord(param)) {
    const key = "key" in param && typeof param.key === "string" ? param.key : defaultKey;
    const values = "values" in param && isRecord(param.values) ? param.values : {};
    return { key, values };
  }
  return { key: defaultKey, values: {} };
};
var defaultNs = "zod";
var makeZodI18nMap = (option) => (issue, ctx) => {
  const { t, ns, handlePath } = {
    t: import_i18next.default.t,
    ns: defaultNs,
    ...option,
    handlePath: option?.handlePath !== false ? {
      context: "with_path",
      ns: option?.ns ?? defaultNs,
      keyPrefix: void 0,
      ...option?.handlePath
    } : null
  };
  let message;
  message = (0, import_zod.defaultErrorMap)(issue, ctx).message;
  const path = issue.path.length > 0 && !!handlePath ? {
    context: handlePath.context,
    path: t(
      [handlePath.keyPrefix, issue.path.join(".")].filter(Boolean).join("."),
      {
        ns: handlePath.ns,
        defaultValue: issue.path.join(".")
      }
    )
  } : {};
  switch (issue.code) {
    case import_zod.ZodIssueCode.invalid_type:
      if (issue.received === import_zod.ZodParsedType.undefined) {
        message = t("errors.invalid_type_received_undefined", {
          ns,
          defaultValue: message,
          ...path
        });
      } else if (issue.received === import_zod.ZodParsedType.null) {
        message = t("errors.invalid_type_received_null", {
          ns,
          defaultValue: message,
          ...path
        });
      } else {
        message = t("errors.invalid_type", {
          expected: t(`types.${issue.expected}`, {
            defaultValue: issue.expected,
            ns
          }),
          received: t(`types.${issue.received}`, {
            defaultValue: issue.received,
            ns
          }),
          ns,
          defaultValue: message,
          ...path
        });
      }
      break;
    case import_zod.ZodIssueCode.invalid_literal:
      message = t("errors.invalid_literal", {
        expected: JSON.stringify(issue.expected, jsonStringifyReplacer),
        ns,
        defaultValue: message,
        ...path
      });
      break;
    case import_zod.ZodIssueCode.unrecognized_keys:
      message = t("errors.unrecognized_keys", {
        keys: joinValues(issue.keys, ", "),
        count: issue.keys.length,
        ns,
        defaultValue: message,
        ...path
      });
      break;
    case import_zod.ZodIssueCode.invalid_union:
      message = t("errors.invalid_union", {
        ns,
        defaultValue: message,
        ...path
      });
      break;
    case import_zod.ZodIssueCode.invalid_union_discriminator:
      message = t("errors.invalid_union_discriminator", {
        options: joinValues(issue.options),
        ns,
        defaultValue: message,
        ...path
      });
      break;
    case import_zod.ZodIssueCode.invalid_enum_value:
      message = t("errors.invalid_enum_value", {
        options: joinValues(issue.options),
        received: issue.received,
        ns,
        defaultValue: message,
        ...path
      });
      break;
    case import_zod.ZodIssueCode.invalid_arguments:
      message = t("errors.invalid_arguments", {
        ns,
        defaultValue: message,
        ...path
      });
      break;
    case import_zod.ZodIssueCode.invalid_return_type:
      message = t("errors.invalid_return_type", {
        ns,
        defaultValue: message,
        ...path
      });
      break;
    case import_zod.ZodIssueCode.invalid_date:
      message = t("errors.invalid_date", {
        ns,
        defaultValue: message,
        ...path
      });
      break;
    case import_zod.ZodIssueCode.invalid_string:
      if (typeof issue.validation === "object") {
        if ("startsWith" in issue.validation) {
          message = t(`errors.invalid_string.startsWith`, {
            startsWith: issue.validation.startsWith,
            ns,
            defaultValue: message,
            ...path
          });
        } else if ("endsWith" in issue.validation) {
          message = t(`errors.invalid_string.endsWith`, {
            endsWith: issue.validation.endsWith,
            ns,
            defaultValue: message,
            ...path
          });
        }
      } else {
        message = t(`errors.invalid_string.${issue.validation}`, {
          validation: t(`validations.${issue.validation}`, {
            defaultValue: issue.validation,
            ns
          }),
          ns,
          defaultValue: message,
          ...path
        });
      }
      break;
    case import_zod.ZodIssueCode.too_small:
      const minimum = issue.type === "date" ? new Date(issue.minimum) : issue.minimum;
      message = t(
        `errors.too_small.${issue.type}.${issue.exact ? "exact" : issue.inclusive ? "inclusive" : "not_inclusive"}`,
        {
          minimum,
          count: typeof minimum === "number" ? minimum : void 0,
          ns,
          defaultValue: message,
          ...path
        }
      );
      break;
    case import_zod.ZodIssueCode.too_big:
      const maximum = issue.type === "date" ? new Date(issue.maximum) : issue.maximum;
      message = t(
        `errors.too_big.${issue.type}.${issue.exact ? "exact" : issue.inclusive ? "inclusive" : "not_inclusive"}`,
        {
          maximum,
          count: typeof maximum === "number" ? maximum : void 0,
          ns,
          defaultValue: message,
          ...path
        }
      );
      break;
    case import_zod.ZodIssueCode.custom:
      const { key, values } = getKeyAndValues(
        issue.params?.i18n,
        "errors.custom"
      );
      message = t(key, {
        ...values,
        ns,
        defaultValue: message,
        ...path
      });
      break;
    case import_zod.ZodIssueCode.invalid_intersection_types:
      message = t("errors.invalid_intersection_types", {
        ns,
        defaultValue: message,
        ...path
      });
      break;
    case import_zod.ZodIssueCode.not_multiple_of:
      message = t("errors.not_multiple_of", {
        multipleOf: issue.multipleOf,
        ns,
        defaultValue: message,
        ...path
      });
      break;
    case import_zod.ZodIssueCode.not_finite:
      message = t("errors.not_finite", {
        ns,
        defaultValue: message,
        ...path
      });
      break;
    default:
  }
  return { message };
};
var zodI18nMap = makeZodI18nMap();
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  makeZodI18nMap,
  zodI18nMap
});
//# sourceMappingURL=index.js.map