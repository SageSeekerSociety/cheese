import { ZodErrorMap } from 'zod';
import { i18n } from 'i18next';

type MakeZodI18nMap = (option?: ZodI18nMapOption) => ZodErrorMap;
type ZodI18nMapOption = {
    t?: i18n["t"];
    ns?: string | readonly string[];
    handlePath?: HandlePathOption | false;
};
type HandlePathOption = {
    context?: string;
    ns?: string | readonly string[];
    keyPrefix?: string;
};
declare const makeZodI18nMap: MakeZodI18nMap;
declare const zodI18nMap: ZodErrorMap;

export { HandlePathOption, MakeZodI18nMap, ZodI18nMapOption, makeZodI18nMap, zodI18nMap };
