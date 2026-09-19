import { ApiClient } from './api';

export function CartView() {
  return <div>{new ApiClient().get('/cart')}</div>;
}

export const Badge = () => <span />;
