module Shop.Cart where

data Cart = Cart [String]

class Priced a where
  price :: a -> Int

add :: Cart -> String -> Cart
add (Cart items) item = if null item then Cart items else Cart (item : items)
