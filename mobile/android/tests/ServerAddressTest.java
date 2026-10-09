import com.mysiya.devflow.ServerAddress;
public final class ServerAddressTest {
    private static int checks=0;
    private static void check(boolean value){if(!value)throw new AssertionError("Address policy case "+checks);checks++;}
    public static void main(String[] args){
        check(ServerAddress.normalize(" https://Example.com:443/ ").equals("https://example.com/"));
        check(ServerAddress.normalize("https://example.com:8443").equals("https://example.com:8443/"));
        for(String invalid:new String[]{"http://example.com","javascript:alert(1)","file:///tmp","https://user:password@example.com","https://example.com/api","https://example.com?token=abc","https://example.com#secret","https://example.com:0","https://example.com:65536","https://example.com\n","https://",""}){
            try{ServerAddress.normalize(invalid);throw new AssertionError("Accepted invalid address");}catch(IllegalArgumentException expected){checks++;}
        }
        check(ServerAddress.sameOrigin("https://example.com/","https://EXAMPLE.com:443/api/runs?left=one"));
        for(String invalid:new String[]{"https://example.com.evil.test/api","https://evil-example.com/api","https://example.com:8443/api","http://example.com/api","https://user@example.com/api","//example.com/api","data:text/html,content","intent://example.com","https://example.com%2eevil.test/api"})check(!ServerAddress.sameOrigin("https://example.com/",invalid));
        check(ServerAddress.externalHttps("https://github.com/Mysiya/DevFlow"));
        check(!ServerAddress.externalHttps("https://user:secret@github.com/"));
        System.out.println(checks+" address policy checks passed");
    }
}
