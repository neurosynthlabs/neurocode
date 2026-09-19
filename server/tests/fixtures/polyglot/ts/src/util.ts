export function formatMoney(value: number): string {
  return value > 0 ? `$${value}` : '-';
}

export interface Money {
  amount: number;
}

export type Currency = 'USD' | 'EUR';

export enum Status {
  Open,
  Paid,
}

export const TAX_RATE = 0.2;

const hidden = 1;
export const DOUBLE_RATE = hidden * 2;
