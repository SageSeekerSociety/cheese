/**
 * Type definition for language messages.
 * It represents a record where the key is the language code,
 * and the value is another record of message keys and their corresponding translations.
 */
type LangMessages = Record<string, Record<string, string>>;
/**
 * Default language message records.
 * It contains all the imported language message files.
 */
export declare const DEFAULT_LOCALE_MESSAGE: LangMessages;
/**
 * Locale class for managing language and message translations.
 */
declare class Locale {
    /**
     * Mitt event emitter for handling language change events.
     */
    private emitter;
    /**
     * Private property to store the current locale settings.
     */
    private _locale;
    /**
     * Get the current language code.
     * @returns The current language code.
     */
    get lang(): string;
    /**
     * Set the current language code.
     * If the provided language is not supported, it sets the language to the fallback language.
     * @param lang - The new language code to set.
     */
    set lang(lang: string);
    /**
     * Get the fallback language code.
     * @returns The fallback language code.
     */
    get fallbackLang(): string;
    /**
     * Set the fallback language code.
     * If the provided language is not supported, it logs a warning and keeps the current fallback language.
     * @param lang - The new fallback language code to set.
     */
    set fallbackLang(lang: string);
    /**
     * Get the language message records.
     * @returns The language message records.
     */
    get message(): LangMessages;
    /**
     * Set the language message records.
     * @param message - The new language message records to set.
     */
    set message(message: LangMessages);
    /**
     * Load the message records for a specific language.
     * @param lang - The language code to load messages for.
     * @returns The message records for the specified language.
     */
    loadLangMessage(lang: string): Record<string, string>;
    /**
     * Check if a given language is supported.
     * @param lang - The language code to check.
     * @returns True if the language is supported, false otherwise.
     */
    private isLangSupported;
    /**
     * Public method to set the current language.
     * @param lang - The new language code to set.
     */
    setLang(lang: string): void;
    /**
     * Public method to set the fallback language.
     * @param lang - The new fallback language code to set.
     */
    setFallbackLang(lang: string): void;
    /**
     * Public method to register a callback for language change events.
     * @param hook - The callback function to be called when the language changes.
     * @returns An object with an unsubscribe method to stop listening to the event.
     */
    registerWatchLang(hook: (lang: string) => void): {
        unsubscribe: () => void;
    };
    /**
     * Public method to set the message records for a specific language.
     * @param lang - The language code to set messages for.
     * @param message - The message records to set for the specified language.
     */
    setMessage(lang: string, message: Record<string, string>): void;
    /**
     * Build an internationalization handler function.
     * @param lang - Optional language code. If not provided, the current language will be used.
     * @returns A function that takes a message path and returns the translated message.
     */
    buildI18nHandler(lang?: string): (path: string) => string;
}
/**
 * An instance of the Locale class for global use.
 */
export declare const locale: Locale;
/**
 * A Vue composable function for using the locale functionality in a reactive way.
 * @returns An object containing the reactive language code and the computed translation function.
 */
export declare const useLocale: () => {
    lang: import('vue').Ref<string, string>;
    t: import('vue').ComputedRef<(path: string) => string>;
};
export {};
