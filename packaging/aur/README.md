# AUR packaging

`PKGBUILD` + `.SRCINFO` for the [`slow-coffee`](https://aur.archlinux.org/packages/slow-coffee)
AUR package. CI keeps these in sync with releases; submit manually like so.

## First submission (reserves the name)

You need an AUR account with an SSH key registered under *My Account*.

```sh
git clone ssh://aur@aur.archlinux.org/slow-coffee.git aur-slow-coffee
cd aur-slow-coffee
cp ../packaging/aur/PKGBUILD .
# pin the source checksum to the tagged tarball, then regenerate .SRCINFO:
updpkgsums
makepkg --printsrcinfo > .SRCINFO
git add PKGBUILD .SRCINFO
git commit -m "Initial import: slow-coffee 1.0.0"
git push
```

## Updating for a new release

Bump `pkgver` (reset `pkgrel=1`), then `updpkgsums && makepkg --printsrcinfo > .SRCINFO`,
commit, push. Test with `makepkg -si` before pushing.
