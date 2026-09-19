import { formatMoney } from './util';
import React from 'react';

export class ApiClient {
  get(path: string): string {
    if (path && path.length > 1) {
      return formatMoney(path.length);
    }
    return '';
  }
}

export const load = async (id: string) => {
  return new ApiClient().get(id);
};

export const reactVersion = React.version;
