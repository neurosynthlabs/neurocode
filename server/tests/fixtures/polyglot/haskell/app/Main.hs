module Main where

import Shop.Cart
import Data.List (sort)

main :: IO ()
main = print (length [add (Cart []) "apple"])
