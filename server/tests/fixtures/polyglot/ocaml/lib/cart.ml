type t = { items : string list }

let add cart item = if item = "" then cart else { items = item :: cart.items }

module Store = struct
  let open_ () = ()
end
