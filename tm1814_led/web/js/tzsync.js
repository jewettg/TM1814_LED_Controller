// Keeps the display time zone (from Settings) in sync for date formatting.
import { getSettings } from './api.js';
import { setDisplayTz } from './ui.js';
export { setDisplayTz };
export async function getSettingsSafe() { try { return await getSettings(); } catch (e) { return null; } }
