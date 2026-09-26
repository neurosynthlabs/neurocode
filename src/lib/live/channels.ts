import { request } from '@/lib/api';

/* Chat channels: Telegram today (GET /channels, PUT /channels/telegram, POST|DELETE /channels/telegram/link).
   An admin gives the bot's token; each person links their own chat with a one-time code, and from then on
   the gates they may decide arrive there with their answers as buttons. */

export interface TelegramStatus {
  configured: boolean;
  /** `@name_bot`, once a token is set. */
  bot: string | null;
  /** Whether *your* chat is linked, and its Telegram username when it has one. */
  linked: boolean;
  chat: string | null;
  /** Admins only: how many people have linked a chat. */
  linkedPeople?: number;
}

export interface Channels { telegram: TelegramStatus }

export interface LinkCode { code: string; url: string; expiresAt: string }

export const channelsApi = {
  status: () => request<Channels>('/channels'),
  setToken: (token: string) => request<Channels>('/channels/telegram', { method: 'PUT', json: { token } }),
  link: () => request<LinkCode>('/channels/telegram/link', { method: 'POST' }),
  unlink: () => request<Channels>('/channels/telegram/link', { method: 'DELETE' }),
};
