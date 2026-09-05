class Lame < Formula
  desc "High quality MPEG Audio Layer III (MP3) encoder"
  homepage "https://lame.sourceforge.io/"
  url "https://downloads.sourceforge.net/project/lame/lame/3.101/lame-3.101.tar.gz"
  sha256 "7578af6eebd578b2bd64e468fac4ae1f03670a7e028166e67f855674b9b6aeac"
  license "LGPL-2.0-or-later"

  livecheck do
    url :stable
    regex(%r{url=.*?/lame[._-]v?(\d+(?:\.\d+)+)\.t}i)
  end

  depends_on "pkgconf" => :build
  depends_on "mpg123"

  uses_from_macos "ncurses"

  def install
    # lame.h leaves id3tag ucs2 helpers parse.c calls undeclared, but they are still exported
    ENV.append_to_cflags "-Wno-implicit-function-declaration"

    system "./configure", "--disable-dependency-tracking",
                          "--disable-debug",
                          "--prefix=#{prefix}",
                          "--enable-nasm"
    system "make", "install"
  end

  test do
    system bin/"lame", "--genre-list", test_fixtures("test.mp3")
  end
end
