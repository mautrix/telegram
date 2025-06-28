package example

//go:generate go run go.mau.fi/mautrix-telegram/pkg/gotd/cmd/rsagen -f single.pem -single -pkg example -var SinglePK -o rsagen_single.go
//go:generate go run go.mau.fi/mautrix-telegram/pkg/gotd/cmd/rsagen -f single.pem -single -pkg example -var SinglePK -o rsagen_single_test.go -exec test.tmpl

//go:generate go run go.mau.fi/mautrix-telegram/pkg/gotd/cmd/rsagen -f many.pem -pkg example -var ManyPK -o rsagen_many.go
//go:generate go run go.mau.fi/mautrix-telegram/pkg/gotd/cmd/rsagen -f many.pem -pkg example -var ManyPK -o rsagen_many_test.go -exec test.tmpl

//go:generate go run go.mau.fi/mautrix-telegram/pkg/gotd/cmd/rsagen -pkg example -var EmptyPK -o rsagen_empty.go
//go:generate go run go.mau.fi/mautrix-telegram/pkg/gotd/cmd/rsagen -pkg example -var EmptyPK -o rsagen_empty_test.go -exec test.tmpl
